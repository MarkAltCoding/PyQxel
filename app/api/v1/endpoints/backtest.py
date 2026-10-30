"""Strategy backtesting routes, and the stored results they leave behind."""

import asyncio
import logging
from collections.abc import Callable
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
from app.data.fetcher import DataFetchError, SymbolNotFoundError, fetch_price_history
from app.data.panel import fetch_close_panel
from app.db.backtests import delete_backtest, get_backtest, list_backtests, save_backtest
from app.db.session import get_session
from app.models.backtest import (
    BacktestList,
    BacktestOptions,
    BacktestRequest,
    BacktestResponse,
    BacktestStrategy,
    CrossSectionalBacktestRequest,
    CrossSectionalBacktestResponse,
    CustomBacktestRequest,
    CustomStrategy,
    DualMomentum,
    EquityPoint,
    BacktestStrategyType,
    AssetWeight,
    InformationCoefficient,
    MultiAssetCustomBacktestRequest,
    RankedAsset,
)
from app.models.factors import RISK_FREE, FactorContext, FactorModel
from app.models.stock import SYMBOL_PATTERN, HistoryCoverage
from app.stats.backtest import (
    BacktestResult,
    MisalignedPositionsError,
    Strategy,
    cash_returns,
    replay_positions,
    replay_weights,
    run_backtest,
    run_portfolio_backtest,
    strategy_for,
)
from app.stats.copulas import asynchronous_trading_warning
from app.stats.cross_section import CrossSectionalResult, run_cross_sectional_backtest
from app.stats.factors import fit_factor_returns
from app.stats.panel import first_date
from app.stats.volatility import InsufficientDataError

logger = logging.getLogger(__name__)

router = APIRouter()
"""Routes under ``/stocks`` that run backtests."""

results_router = APIRouter()
"""Routes under ``/backtests`` that read and delete stored results."""

Session = Annotated[AsyncSession, Depends(get_session)]

StrategyBuilder = Callable[[pd.Series, pd.Series | None, int], tuple[pd.Series, Strategy]]
"""Given the symbol's closes, a benchmark's closes if the strategy needs one, and bars per
year: the closes to backtest and the strategy to run on them."""


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


async def load_benchmark(spec: BacktestStrategy, period: str, interval: str) -> pd.Series | None:
    """Fetch the closes dual momentum compares against, or ``None`` for other strategies.

    Raises:
        HTTPException: 422 when the benchmark is unknown or has no bars in the window, and
            the provider's own status, naming the benchmark, when it cannot be fetched.
    """
    if not isinstance(spec, DualMomentum):
        return None
    try:
        frame = await fetch_price_history(spec.benchmark, period=period, interval=interval)
    except SymbolNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Dual momentum's benchmark {spec.benchmark} is not a known symbol.",
        ) from exc
    except DataFetchError as exc:
        error = upstream_error(exc)
        raise HTTPException(
            status_code=error.status_code,
            detail=f"Dual momentum's benchmark {spec.benchmark} could not be fetched: {exc}",
        ) from exc
    if frame.empty:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Dual momentum's benchmark {spec.benchmark} has no prices in this window.",
        )
    return frame["Close"]


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


def equity_points(result: BacktestResult | CrossSectionalResult) -> list[EquityPoint]:
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


async def backtest_and_store(
    symbol: str,
    options: BacktestOptions,
    spec: BacktestStrategy,
    build: StrategyBuilder,
    session: AsyncSession,
    user_id: str,
) -> BacktestResponse:
    """Fetch the bars and factor data a backtest needs, score ``build``'s strategy on them,
    and store the result.

    Raises:
        HTTPException: The statuses documented on the backtest routes.
    """
    model = options.attribution if options.interval == "1d" else None
    frame_result, factors_result, benchmark_result = await asyncio.gather(
        fetch_price_history(symbol, period=options.period, interval=options.interval),
        load_factors(model, options.cash_interest),
        load_benchmark(spec, options.period, options.interval),
        return_exceptions=True,
    )
    if isinstance(frame_result, DataFetchError):
        raise upstream_error(frame_result) from frame_result
    for outcome in (frame_result, factors_result, benchmark_result):
        if isinstance(outcome, BaseException):
            raise outcome
    assert isinstance(frame_result, pd.DataFrame) and isinstance(factors_result, tuple)
    assert benchmark_result is None or isinstance(benchmark_result, pd.Series)
    factors, factor_notice = factors_result
    symbol = symbol.upper()
    coverage, coverage_notice = history_coverage(
        symbol, options.period, options.interval, frame_result
    )
    periods_per_year = PERIODS_PER_YEAR[options.interval]
    try:
        closes, strategy = build(frame_result["Close"], benchmark_result, periods_per_year)
        result = run_backtest(
            closes,
            strategy,
            periods_per_year,
            cost_bps=options.cost_bps,
            risk_free_rate=options.risk_free_rate,
            risk_free=(
                factors[RISK_FREE] if options.cash_interest and factors is not None else None
            ),
        )
    except (InsufficientDataError, MisalignedPositionsError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    attribution, attribution_notice = attribute(result.returns, factors, model)
    if options.attribution is not None and options.interval != "1d":
        attribution_notice = "Factor attribution needs daily bars, so it was left out."
    notice = " ".join(part for part in (coverage_notice, factor_notice, attribution_notice) if part)

    response = BacktestResponse(
        symbol=symbol,
        period=options.period,
        interval=options.interval,
        periods_per_year=periods_per_year,
        strategy=spec,
        cost_bps=options.cost_bps,
        risk_free_rate=options.risk_free_rate,
        metrics=result.metrics,
        benchmark=result.benchmark,
        trades=result.trades,
        exposure=result.exposure,
        cash_interest=options.cash_interest,
        attribution=attribution,
        equity_curve=equity_points(result),
        coverage=coverage,
        notice=notice or None,
    )
    return await _store(session, user_id, response)


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
    the asset's own past return is positive), ``mean_reversion`` (buy a stretched fall
    below the moving average, sell as it reverts), ``rsi`` (buy oversold, sell
    overbought), ``bollinger_bands`` (fade or follow moves to the bands),
    ``donchian_breakout`` (buy new highs, sell new lows), ``dual_momentum`` (long while
    the past return is positive and beats a benchmark's), ``volatility_targeting``
    (long, sized to a target volatility) and ``macd`` (long while MACD is above its
    signal line). All need only adjusted closes.

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
    strategy or dual momentum's benchmark is unknown, and 502 when the data provider
    fails, or the risk-free rate or dual momentum's benchmark cannot be fetched.
    """
    return await backtest_and_store(
        symbol,
        request,
        request.strategy,
        lambda closes, benchmark, periods_per_year: (
            closes,
            strategy_for(request.strategy, periods_per_year, benchmark),
        ),
        session,
        user.id,
    )


def _equal_weights(request: MultiAssetCustomBacktestRequest) -> dict[str, float]:
    """Equal weights in the symbols the strategy ever holds, the benchmark it is measured
    against; a symbol it only reads as a signal, such as an index, is not one to hold."""
    traded = [
        symbol
        for j, symbol in enumerate(request.symbols)
        if any(row[j] != 0 for row in request.weights)
    ] or list(request.symbols)
    return {symbol: 1.0 / len(traded) for symbol in traded}


@router.post(
    "/{symbol}/backtest/custom",
    response_model=BacktestResponse,
    summary="Score the positions of a custom strategy",
)
async def create_custom_backtest(
    symbol: Symbol,
    session: Session,
    user: CurrentUser,
    request: Annotated[CustomBacktestRequest, Body()],
) -> BacktestResponse:
    """Score positions a custom Python strategy produced in the browser, as
    ``POST /stocks/{symbol}/backtest`` scores a built-in one.

    The code never runs on the server: it is stored with the result so the run can be
    reproduced and edited. The server fetches ``symbol``'s bars again and backtests the
    ones from the first of ``timestamps`` to the last, so a bar published since the
    positions were computed is left out. Each position is held over the bar after its
    timestamp, and costs, cash interest and attribution work as for built-in strategies.

    Returns 404 for unknown symbols, 422 when a bar in that range has no position or the
    range has too few bars, and 502 when the data provider fails or the risk-free rate
    cannot be fetched.
    """
    return await backtest_and_store(
        symbol,
        request,
        CustomStrategy(language=request.language, code=request.code),
        lambda closes, _benchmark, _periods_per_year: replay_positions(
            closes, request.timestamps, request.positions
        ),
        session,
        user.id,
    )


async def _store(
    session: AsyncSession, user_id: str, response: BacktestResponse
) -> BacktestResponse:
    """Store ``response``, or return it without an ``id`` if the database is unavailable."""
    try:
        return await save_backtest(session, user_id, response)
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.error("Could not store the %s backtest: %s", response.symbol, exc)
        return response


def _shared_coverage(
    closes: pd.DataFrame, window: pd.DataFrame, options: BacktestOptions
) -> tuple[HistoryCoverage, str | None]:
    """How much of the requested window the bars every symbol traded cover, naming the
    symbol whose data starts last when they start late."""
    coverage, notice = history_coverage(
        ", ".join(str(symbol) for symbol in closes.columns),
        options.period,
        options.interval,
        window,
    )
    if coverage == "partial" and not window.empty:
        latest = max(closes.columns, key=lambda symbol: first_date(closes[str(symbol)]))
        notice = (
            f"The backtest starts {pd.Timestamp(window.index[0]).date()}, when {latest}'s "
            "data begins: weights are only scored on bars every symbol traded."
        )
    return coverage, notice


@results_router.post(
    "/custom",
    response_model=BacktestResponse,
    summary="Score a custom strategy's weights across several symbols",
)
async def create_multi_asset_custom_backtest(
    session: Session,
    user: CurrentUser,
    request: Annotated[MultiAssetCustomBacktestRequest, Body()],
) -> BacktestResponse:
    """Score the weights a custom strategy set across several ``symbols`` in the browser.

    The code never runs on the server: it is stored with the result so the run can be
    reproduced and edited. The server fetches every symbol's bars again, keeps the dates
    all of them traded from the first of ``dates`` to the last, and holds each row of
    ``weights`` over the bar after its date. Weights are shares of capital, reset every
    bar; what they leave over is cash, which earns nothing or, with ``cash_interest``,
    the risk-free rate, as do short sale proceeds. Each change of weight pays
    ``cost_bps`` per unit traded. The benchmark holds the symbols the weights ever hold
    (all of them, if none) in equal weights,
    rebalanced every bar, without costs; ``assets`` gives each symbol's part.

    The result is stored like a single-symbol backtest, under its first symbol, and its
    ``id`` returned for ``GET /backtests/{id}``.

    Returns 404 naming unknown symbols, 422 when a bar every symbol traded in that range
    has no weights or the range has too few bars, and 502 when the data provider fails
    or the risk-free rate cannot be fetched.
    """
    model = request.attribution if request.interval == "1d" else None
    panel_result, factors_result = await asyncio.gather(
        fetch_close_panel(request.symbols, period=request.period, interval=request.interval),
        load_factors(model, request.cash_interest),
        return_exceptions=True,
    )
    if isinstance(panel_result, DataFetchError):
        raise upstream_error(panel_result) from panel_result
    for outcome in (panel_result, factors_result):
        if isinstance(outcome, BaseException):
            raise outcome
    assert not isinstance(panel_result, BaseException)
    assert isinstance(factors_result, tuple)
    factors, factor_notice = factors_result
    periods_per_year = PERIODS_PER_YEAR[request.interval]
    try:
        prices, held = replay_weights(panel_result.closes, request.dates, request.weights)
        result = await asyncio.to_thread(
            run_portfolio_backtest,
            prices,
            {symbol: 1.0 for symbol in request.symbols},
            lambda closes: held[closes.name],
            periods_per_year,
            cost_bps=request.cost_bps,
            risk_free_rate=request.risk_free_rate,
            cash=(
                cash_returns(factors[RISK_FREE], prices.index)
                if request.cash_interest and factors is not None
                else None
            ),
            benchmark_weights=_equal_weights(request),
        )
    except (InsufficientDataError, MisalignedPositionsError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    coverage, coverage_notice = _shared_coverage(panel_result.closes, prices, request)
    attribution, attribution_notice = attribute(result.returns, factors, model)
    if request.attribution is not None and request.interval != "1d":
        attribution_notice = "Factor attribution needs daily bars, so it was left out."
    notices = [
        coverage_notice,
        asynchronous_trading_warning(panel_result.timezones),
        factor_notice,
        attribution_notice,
    ]
    response = BacktestResponse(
        symbol=request.symbols[0],
        symbols=request.symbols,
        period=request.period,
        interval=request.interval,
        periods_per_year=periods_per_year,
        strategy=CustomStrategy(language=request.language, code=request.code),
        cost_bps=request.cost_bps,
        risk_free_rate=request.risk_free_rate,
        metrics=result.metrics,
        benchmark=result.benchmark,
        trades=result.trades,
        exposure=result.exposure,
        cash_interest=request.cash_interest,
        attribution=attribution,
        assets=[
            AssetWeight(
                symbol=symbol,
                average_weight=float(result.positions[symbol].mean()),
                exposure=part.exposure,
                contribution=part.contribution,
                trades=part.trades,
            )
            for symbol, part in result.assets.items()
        ],
        equity_curve=equity_points(result),
        coverage=coverage,
        notice=" ".join(notice for notice in notices if notice) or None,
    )
    return await _store(session, user.id, response)


LATE_RANKING: pd.Timedelta = pd.Timedelta(days=31)
"""How long after the first rebalance a stock must first be ranked to be named as late."""


def _ranking_notice(result: CrossSectionalResult) -> str | None:
    """Name the stocks never ranked, and those ranked only well after the first rebalance."""
    first = pd.Timestamp(result.equity.index[0])
    never = [symbol for symbol, part in result.assets.items() if part.first_ranked is None]
    late = [
        f"{symbol} from {part.first_ranked.date()}"
        for symbol, part in result.assets.items()
        if part.first_ranked is not None and part.first_ranked - first > LATE_RANKING
    ]
    parts = []
    if never:
        parts.append(f"{', '.join(never)} never had enough history in the window to be ranked.")
    if late:
        parts.append(
            f"Some stocks joined the ranking only once they had enough history: "
            f"{'; '.join(late)}."
        )
    return " ".join(parts) or None


@results_router.post(
    "/cross-sectional",
    response_model=CrossSectionalBacktestResponse,
    summary="Backtest ranking a universe of stocks against one another",
)
async def create_cross_sectional_backtest(
    request: Annotated[CrossSectionalBacktestRequest, Body()],
) -> CrossSectionalBacktestResponse:
    """Rank ``symbols`` by ``signal`` at the last close of every week, month or quarter, and
    hold the best of them until the next ranking.

    Signals: ``momentum`` (the return from ``lookback`` to ``skip`` bars ago, highest
    first), ``reversal`` (the last ``lookback`` bars' return, lowest first),
    ``low_volatility`` (the volatility of daily returns, lowest first), ``trend`` (the
    distance above the moving average, highest first) and ``risk_adjusted_momentum``
    (momentum over its volatility). The top ``quantile`` of the stocks ranked, or the top
    ``top_n``, are bought; with ``long_short`` as many of the bottom ones are shorted, each
    leg one unit of capital. Legs are split equally or by inverse volatility.

    Stocks are ranked once they have the signal's full window, so recent listings join
    later rather than shortening the window for all. Weights drift between rebalances;
    each rebalance pays ``cost_bps`` per unit of capital traded. Capital not invested and
    short sale proceeds earn nothing, or the risk-free rate with ``cash_interest``. The
    benchmark holds every stock equally, rebalanced daily, without costs.

    ``information_coefficient`` is the average Spearman correlation of each ranking with
    the returns to the next one, and ``attribution`` regresses the daily returns on a
    factor model. Results are not stored.

    Returns 404 naming unknown symbols, 422 when no rebalance has enough stocks with the
    signal's history, and 502 when the price provider fails or the risk-free rate cannot
    be fetched.
    """
    panel_result, factors_result = await asyncio.gather(
        fetch_close_panel(request.symbols, period=request.period, interval="1d"),
        load_factors(request.attribution, request.cash_interest),
        return_exceptions=True,
    )
    if isinstance(panel_result, DataFetchError):
        raise upstream_error(panel_result) from panel_result
    for outcome in (panel_result, factors_result):
        if isinstance(outcome, BaseException):
            raise outcome
    assert not isinstance(panel_result, BaseException)
    assert isinstance(factors_result, tuple)
    factors, factor_notice = factors_result
    try:
        result = await asyncio.to_thread(
            run_cross_sectional_backtest,
            panel_result.closes,
            request.signal,
            rebalance=request.rebalance,
            quantile=request.quantile,
            top_n=request.top_n,
            long_short=request.long_short,
            weighting=request.weighting,
            volatility_window=request.volatility_window,
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

    attribution, attribution_notice = attribute(result.returns, factors, request.attribution)
    notices = [
        _ranking_notice(result),
        asynchronous_trading_warning(panel_result.timezones),
        factor_notice,
        attribution_notice,
    ]
    information = result.information_coefficient
    return CrossSectionalBacktestResponse(
        symbols=request.symbols,
        signal=request.signal,
        rebalance=request.rebalance,
        quantile=request.quantile,
        top_n=request.top_n,
        long_short=request.long_short,
        weighting=request.weighting,
        period=request.period,
        cost_bps=request.cost_bps,
        risk_free_rate=request.risk_free_rate,
        cash_interest=request.cash_interest,
        observations=len(result.returns),
        rebalances=result.rebalances,
        holdings_per_leg=result.holdings_per_leg,
        turnover=result.turnover,
        exposure=result.exposure,
        metrics=result.metrics,
        benchmark=result.benchmark,
        information_coefficient=(
            None
            if information is None
            else InformationCoefficient(
                mean=information.mean,
                t_stat=information.t_stat,
                hit_rate=information.hit_rate,
                observations=information.observations,
            )
        ),
        assets=[
            RankedAsset(
                symbol=symbol,
                contribution=part.contribution,
                long_share=part.long_share,
                short_share=part.short_share,
                first_ranked=(
                    None if part.first_ranked is None else part.first_ranked.to_pydatetime()
                ),
                score=part.score,
                weight=part.weight,
            )
            for symbol, part in result.assets.items()
        ],
        attribution=attribution,
        equity_curve=equity_points(result),
        notice=" ".join(notice for notice in notices if notice) or None,
    )


@results_router.get("", response_model=BacktestList, summary="List stored backtests")
async def read_backtests(
    session: Session,
    user: CurrentUser,
    symbol: Annotated[
        str | None, Query(pattern=SYMBOL_PATTERN, description="Only this symbol's results.")
    ] = None,
    strategy: Annotated[
        BacktestStrategyType | None, Query(description="Only results of this strategy type.")
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
