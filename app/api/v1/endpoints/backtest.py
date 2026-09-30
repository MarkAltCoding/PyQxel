"""Strategy backtesting routes."""

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, status

from app.api.v1.endpoints.stocks import (
    PERIODS_PER_YEAR,
    Symbol,
    history_coverage,
    upstream_error,
)
from app.data.fetcher import DataFetchError, fetch_price_history
from app.models.backtest import BacktestRequest, BacktestResponse, EquityPoint
from app.stats.backtest import run_backtest, strategy_for
from app.stats.volatility import InsufficientDataError

router = APIRouter()


@router.post(
    "/{symbol}/backtest",
    response_model=BacktestResponse,
    summary="Backtest a strategy against buy-and-hold",
)
async def create_backtest(
    symbol: Symbol,
    request: Annotated[BacktestRequest, Body()] = BacktestRequest(),
) -> BacktestResponse:
    """Backtest a strategy on ``symbol``'s adjusted closes and score it against buy-and-hold.

    Positions set at a bar's close are held over the next bar, trades pay ``cost_bps``
    per unit of turnover, and idle capital earns nothing. Returns are total returns,
    since closes are adjusted for splits and dividends. Returns and volatilities are
    decimals (0.25 = 25%); volatility, Sharpe and Sortino are annualized.

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

    return BacktestResponse(
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
