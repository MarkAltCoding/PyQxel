"""Strategy backtesting routes, and the stored results they leave behind."""

import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.stocks import (
    PERIODS_PER_YEAR,
    Symbol,
    history_coverage,
    upstream_error,
)
from app.data.fetcher import DataFetchError, fetch_price_history
from app.db.backtests import delete_backtest, get_backtest, list_backtests, save_backtest
from app.db.session import get_session
from app.models.backtest import BacktestList, BacktestRequest, BacktestResponse, EquityPoint
from app.models.stock import SYMBOL_PATTERN
from app.stats.backtest import run_backtest, strategy_for
from app.stats.volatility import InsufficientDataError

logger = logging.getLogger(__name__)

router = APIRouter()
"""Routes under ``/stocks`` that run backtests."""

results_router = APIRouter()
"""Routes under ``/backtests`` that read and delete stored results."""

Session = Annotated[AsyncSession, Depends(get_session)]


def _database_error(exc: SQLAlchemyError) -> HTTPException:
    """Log a database failure and translate it into a 503."""
    logger.error("Backtest database request failed: %s", exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="The backtest database is unavailable.",
    )


@router.post(
    "/{symbol}/backtest",
    response_model=BacktestResponse,
    summary="Backtest a strategy against buy-and-hold",
)
async def create_backtest(
    symbol: Symbol,
    session: Session,
    request: Annotated[BacktestRequest, Body()] = BacktestRequest(),
) -> BacktestResponse:
    """Backtest a strategy on ``symbol``'s adjusted closes and score it against buy-and-hold.

    Positions set at a bar's close are held over the next bar, trades pay ``cost_bps``
    per unit of turnover, and idle capital earns nothing. Returns are total returns,
    since closes are adjusted for splits and dividends. Returns and volatilities are
    decimals (0.25 = 25%); volatility, Sharpe and Sortino are annualized.

    The result is stored and its ``id`` returned for ``GET /backtests/{id}``. If the
    database is unavailable the result is still returned, with a null ``id``.

    Returns 404 for unknown symbols, 422 when the window has too few bars for the
    strategy, and 502 when the data provider fails.
    """
    try:
        frame = await fetch_price_history(symbol, period=request.period, interval=request.interval)
    except DataFetchError as exc:
        raise upstream_error(exc) from exc
    symbol = symbol.upper()
    coverage, notice = history_coverage(symbol, request.period, request.interval, frame)
    periods_per_year = PERIODS_PER_YEAR[request.interval]
    try:
        result = run_backtest(
            frame["Close"],
            strategy_for(request.strategy),
            periods_per_year,
            cost_bps=request.cost_bps,
            risk_free_rate=request.risk_free_rate,
        )
    except InsufficientDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    response = BacktestResponse(
        symbol=symbol,
        period=request.period,
        interval=request.interval,
        periods_per_year=periods_per_year,
        strategy=request.strategy,
        cost_bps=request.cost_bps,
        risk_free_rate=request.risk_free_rate,
        metrics=result.metrics,
        benchmark=result.benchmark,
        trades=result.trades,
        exposure=result.exposure,
        equity_curve=[
            EquityPoint(
                timestamp=timestamp.to_pydatetime(),
                strategy=float(strategy),
                benchmark=float(benchmark),
            )
            for timestamp, strategy, benchmark in zip(
                result.equity.index, result.equity, result.benchmark_equity
            )
        ],
        coverage=coverage,
        notice=notice,
    )
    try:
        return await save_backtest(session, response)
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.error("Could not store the %s backtest: %s", symbol, exc)
        return response


@results_router.get("", response_model=BacktestList, summary="List stored backtests")
async def read_backtests(
    session: Session,
    symbol: Annotated[
        str | None, Query(pattern=SYMBOL_PATTERN, description="Only this symbol's results.")
    ] = None,
    strategy: Annotated[
        Literal["buy_and_hold", "sma_crossover"] | None,
        Query(description="Only results of this strategy type."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BacktestList:
    """Return stored backtests' headline metrics, newest first.

    Returns 503 when the database is unavailable.
    """
    try:
        return await list_backtests(session, symbol, strategy, limit, offset)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@results_router.get(
    "/{backtest_id}", response_model=BacktestResponse, summary="A stored backtest in full"
)
async def read_backtest(backtest_id: UUID, session: Session) -> BacktestResponse:
    """Return a stored backtest with its equity curve.

    Returns 404 for unknown IDs and 503 when the database is unavailable.
    """
    try:
        result = await get_backtest(session, backtest_id)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No backtest with ID {backtest_id}."
        )
    return result


@results_router.delete(
    "/{backtest_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a stored backtest",
)
async def remove_backtest(backtest_id: UUID, session: Session) -> Response:
    """Delete a stored backtest.

    Returns 404 for unknown IDs and 503 when the database is unavailable.
    """
    try:
        deleted = await delete_backtest(session, backtest_id)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No backtest with ID {backtest_id}."
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
