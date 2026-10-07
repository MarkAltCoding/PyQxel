"""Strategy backtesting routes, and the stored results they leave behind."""

import asyncio
import logging
from typing import Annotated
from uuid import UUID

import pandas as pd

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.stocks import (
    PERIODS_PER_YEAR,
    Symbol,
    history_coverage,
    upstream_error,
)
from app.api.auth import CurrentUser
from app.data.factors import fetch_factors
from app.data.fetcher import DataFetchError, fetch_price_history
from app.db.backtests import delete_backtest, get_backtest, list_backtests, save_backtest
from app.db.session import get_session
from app.models.backtest import (
    BacktestList,
    BacktestRequest,
    BacktestResponse,
    EquityPoint,
    StrategyType,
)
from app.models.factors import RISK_FREE, FactorContext, FactorModel
from app.models.stock import SYMBOL_PATTERN
from app.stats.backtest import BacktestResult, run_backtest, strategy_for
from app.stats.factors import fit_factor_returns
from app.stats.volatility import InsufficientDataError

logger = logging.getLogger(__name__)

router = APIRouter()
"""Routes under ``/stocks`` that run backtests."""

results_router = APIRouter()
"""Routes under ``/backtests`` that read and delete stored results."""

Session = Annotated[AsyncSession, Depends(get_session)]


async def _no_factors() -> None:
    """Stand in for the factor download when a backtest needs no factor data."""
    return None


async def load_factors(
    model: FactorModel | None, cash_interest: bool
) -> tuple[pd.DataFrame | None, str | None]:
    """Fetch the factor data a backtest needs: ``model``'s factors, or just the risk-free rate.

    Returns:
        The factor table, with ``RF``, or ``None`` when neither is needed or attribution
        alone was asked for and the data is unavailable, with a notice saying so.

    Raises:
        HTTPException: 502 when cash interest was asked for and the risk-free rate
            cannot be fetched.
    """
    if model is None and not cash_interest:
        return None, None
    try:
        return await fetch_factors(model or "ff3"), None
    except DataFetchError as exc:
        if cash_interest:
            raise upstream_error(exc) from exc
        return None, "Factor attribution was left out: the factor data could not be fetched."


def attribute(
    returns: pd.Series, factors: pd.DataFrame | None, model: FactorModel | None
) -> tuple[FactorContext | None, str | None]:
    """Regress daily strategy ``returns`` on ``model``'s factors, or explain why not."""
    if model is None or factors is None:
        return None, None
    try:
        fit = fit_factor_returns(returns, factors, model)
    except InsufficientDataError as exc:
        return None, f"Factor attribution was left out: {exc}"
    end = pd.Timestamp(factors.index[-1]).date()
    return FactorContext(model=model, factor_data_end=end, fit=fit), None


def equity_points(result: BacktestResult) -> list[EquityPoint]:
    """The strategy and benchmark equity curves, bar by bar."""
    return [
        EquityPoint(
            timestamp=pd.Timestamp(timestamp).to_pydatetime(),
            strategy=float(strategy),
            benchmark=float(benchmark),
        )
        for timestamp, strategy, benchmark in zip(
            result.equity.index, result.equity, result.benchmark_equity
        )
    ]


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
    user: CurrentUser,
    request: Annotated[BacktestRequest, Body()] = BacktestRequest(),
) -> BacktestResponse:
    """Backtest a strategy on ``symbol``'s adjusted closes and score it against buy-and-hold.

    Strategies: ``buy_and_hold``, ``sma_crossover``, ``time_series_momentum`` (long while
    the asset's own past return is positive) and ``mean_reversion`` (buy a stretched
    fall below the moving average, sell as it reverts).

    Positions set at a bar's close are held over the next bar, and trades pay
    ``cost_bps`` per unit of turnover. Idle capital earns nothing, or the risk-free rate
    with ``cash_interest``. Returns are total returns, since closes are adjusted for
    splits and dividends. Returns and volatilities are decimals (0.25 = 25%); volatility,
    Sharpe and Sortino are annualized.

    ``attribution`` regresses the strategy's daily returns on a factor model:
    ``attribution.fit.alpha`` is the annualized return the factors do not explain, and
    the betas show how much of the result is factor exposure. It needs daily bars.

    The result is stored and its ``id`` returned for ``GET /backtests/{id}``. If the
    database is unavailable the result is still returned, with a null ``id``.

    Returns 404 for unknown symbols, 422 when the window has too few bars for the
    strategy, and 502 when the data provider fails, or the risk-free rate cannot be
    fetched for ``cash_interest``.
    """
    model = request.attribution if request.interval == "1d" else None
    frame_result, factors_result = await asyncio.gather(
        fetch_price_history(symbol, period=request.period, interval=request.interval),
        load_factors(model, request.cash_interest),
        return_exceptions=True,
    )
    if isinstance(frame_result, DataFetchError):
        raise upstream_error(frame_result) from frame_result
    for outcome in (frame_result, factors_result):
        if isinstance(outcome, BaseException):
            raise outcome
    assert isinstance(frame_result, pd.DataFrame) and isinstance(factors_result, tuple)
    factors, factor_notice = factors_result
    symbol = symbol.upper()
    coverage, coverage_notice = history_coverage(
        symbol, request.period, request.interval, frame_result
    )
    periods_per_year = PERIODS_PER_YEAR[request.interval]
    try:
        result = run_backtest(
            frame_result["Close"],
            strategy_for(request.strategy),
            periods_per_year,
            cost_bps=request.cost_bps,
            risk_free_rate=request.risk_free_rate,
            risk_free=(
                factors[RISK_FREE] if request.cash_interest and factors is not None else None
            ),
        )
    except InsufficientDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    attribution, attribution_notice = attribute(result.returns, factors, model)
    if request.attribution is not None and request.interval != "1d":
        attribution_notice = "Factor attribution needs daily bars, so it was left out."
    notice = " ".join(part for part in (coverage_notice, factor_notice, attribution_notice) if part)

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
        cash_interest=request.cash_interest,
        attribution=attribution,
        equity_curve=equity_points(result),
        coverage=coverage,
        notice=notice or None,
    )
    try:
        return await save_backtest(session, user.id, response)
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.error("Could not store the %s backtest: %s", symbol, exc)
        return response


@results_router.get("", response_model=BacktestList, summary="List stored backtests")
async def read_backtests(
    session: Session,
    user: CurrentUser,
    symbol: Annotated[
        str | None, Query(pattern=SYMBOL_PATTERN, description="Only this symbol's results.")
    ] = None,
    strategy: Annotated[
        StrategyType | None, Query(description="Only results of this strategy type.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BacktestList:
    """Return your stored backtests' headline metrics, newest first.

    Returns 503 when the database is unavailable.
    """
    try:
        return await list_backtests(session, user.id, symbol, strategy, limit, offset)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@results_router.get(
    "/{backtest_id}", response_model=BacktestResponse, summary="A stored backtest in full"
)
async def read_backtest(backtest_id: UUID, session: Session, user: CurrentUser) -> BacktestResponse:
    """Return a stored backtest with its equity curve.

    Returns 404 for unknown IDs and other users' backtests, and 503 when the database is
    unavailable.
    """
    try:
        result = await get_backtest(session, user.id, backtest_id)
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
async def remove_backtest(backtest_id: UUID, session: Session, user: CurrentUser) -> Response:
    """Delete a stored backtest.

    Returns 404 for unknown IDs and other users' backtests, and 503 when the database is
    unavailable.
    """
    try:
        deleted = await delete_backtest(session, user.id, backtest_id)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No backtest with ID {backtest_id}."
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
