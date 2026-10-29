"""Multi-asset routes: how a set of assets moves together, simulated portfolio outcomes,
and Claude's review of the saved portfolio."""

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

import numpy as np
import pandas as pd
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import AIRateLimitError, AnalysisError, Written, anthropic_client
from app.ai.external import PASTED_MODEL, ExternalResponseError, parse_response, render_prompt
from app.ai.portfolio import review_messages, stream_portfolio_review, write_portfolio_review
from app.ai.prompts import PORTFOLIO_SYSTEM_PROMPT
from app.api.auth import CurrentUser
from app.api.v1.endpoints.research import ai_error, anthropic_key, check_ai_limits
from app.core.config import get_settings
from app.api.v1.endpoints.backtest import attribute, equity_points, load_benchmark, load_factors
from app.api.v1.endpoints.stocks import COVERAGE_TOLERANCE, PERIOD_OFFSETS, upstream_error
from app.data.cash import MONEY_MARKET_PRICE, is_money_market
from app.data.fetcher import DataFetchError, fetch_ticker_info
from app.data.panel import fetch_close_panel
from app.db.holdings import get_holdings, get_portfolio_name, list_portfolios, replace_holdings
from app.db.portfolio_analyses import (
    find_recent_portfolio_analysis,
    holdings_key,
    latest_portfolio_analysis,
    save_portfolio_analysis,
)
from app.db.session import get_session, get_sessionmaker
from app.db.simulations import (
    delete_simulation,
    get_simulation,
    list_simulations,
    save_simulation,
)
from app.models.portfolio import (
    CopulaFitResponse,
    CopulaRequest,
    GaussianCopula,
    HoldingShares,
    HoldingsResponse,
    HoldingsUpdate,
    HoldingValue,
    Holding,
    LeftOutHolding,
    Matrix,
    PairDependence,
    PortfolioList,
    PortfolioNumber,
    default_portfolio_name,
    PortfolioPeriod,
    StudentTCopula,
)
from app.models.backtest import (
    AssetBacktest,
    PortfolioBacktestRequest,
    PortfolioBacktestResponse,
)
from app.models.external import PortfolioPromptExport, PortfolioReviewImport
from app.models.factors import RISK_FREE
from app.models.portfolio_analysis import (
    AssetRisk,
    InvestorProfile,
    PairCorrelation,
    PlannedTrade,
    PortfolioAnalysisRequest,
    PortfolioAnalysisResponse,
    PortfolioContext,
    PortfolioReview,
    PortfolioRiskStats,
    RebalancePlan,
)
from app.models.research import AnalysisDelta, AnalysisStreamError
from app.models.simulation import SimulationList, SimulationRequest, SimulationResponse
from app.models.stock import SYMBOL_PATTERN
from app.stats.backtest import cash_returns, run_portfolio_backtest, strategy_for
from app.stats.copulas import CopulaFit, asynchronous_trading_warning, fit_copulas
from app.stats.monte_carlo import SimulationError, simulate_portfolio
from app.stats.panel import ReturnPanel, first_date, return_panel, usable_return_panel
from app.stats.portfolio_risk import portfolio_stats, ranked_pairs
from app.stats.rebalance import CASH, Target, plan_trades
from app.stats.volatility import InsufficientDataError

logger = logging.getLogger(__name__)

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]

PortfolioQuery = Annotated[
    PortfolioNumber, Query(description="Which of your saved portfolios: 1 or 2.")
]

MAX_CONCURRENT_SIMULATIONS: int = 2
"""Simulations computed at once; each occupies a CPU core for up to a few seconds."""

SIMULATION_QUEUE_SECONDS: float = 30.0
"""How long a simulation waits for a free slot before the request is turned away."""

_simulation_slots = asyncio.Semaphore(MAX_CONCURRENT_SIMULATIONS)

STRONG_AIC_DIFFERENCE: float = 10.0
"""AIC gap below which neither copula is clearly better."""


def _matrix(values: np.ndarray) -> Matrix:
    """Convert a numpy matrix to rows of floats."""
    return [[float(value) for value in row] for row in values]


def _window_notice(panel: ReturnPanel, closes: pd.DataFrame, period: PortfolioPeriod) -> str | None:
    """Say when the fit starts well after the requested window, and which asset is why."""
    first = pd.Timestamp(panel.returns.index[0])
    requested = pd.Timestamp.now().normalize() - PERIOD_OFFSETS[period]
    if first - requested <= COVERAGE_TOLERANCE:
        return None
    latest = max(closes.columns, key=lambda symbol: first_date(closes[str(symbol)]))
    return (
        f"The fit starts {first.date()}, not {requested.date()}, because {latest} has no "
        "earlier data; every asset must have a return on each day."
    )


def _warnings(fit: CopulaFit, timezones: dict[str, str | None]) -> list[str]:
    """Collect caveats about the comparison and the data."""
    warnings: list[str] = []
    difference = abs(fit.gaussian_aic - fit.t_aic)
    if difference < STRONG_AIC_DIFFERENCE:
        warnings.append(
            f"The two copulas' AICs differ by only {difference:.1f}, so the data do not "
            "clearly favor either."
        )
    if fit.degrees_of_freedom_at_bound:
        warnings.append(
            "The t copula's degrees of freedom reached the upper bound: the returns show no "
            "more joint extremes than a Gaussian copula implies."
        )
    if asynchronous := asynchronous_trading_warning(timezones):
        warnings.append(asynchronous)
    return warnings


def _response(
    fit: CopulaFit,
    panel: ReturnPanel,
    period: PortfolioPeriod,
    warnings: list[str],
    notice: str | None,
    left_out: list[LeftOutHolding],
) -> CopulaFitResponse:
    """Build the response from a fit."""
    tail = fit.t_tail_dependence
    t_preferred = fit.t_aic < fit.gaussian_aic
    return CopulaFitResponse(
        symbols=fit.symbols,
        period=period,
        start=panel.returns.index[0].date(),
        end=panel.returns.index[-1].date(),
        observations=fit.observations,
        excluded_dates=panel.excluded_dates,
        kendall_tau=_matrix(fit.kendall_tau),
        gaussian=GaussianCopula(
            correlation=_matrix(fit.gaussian_correlation),
            log_likelihood=fit.gaussian_log_likelihood,
            aic=fit.gaussian_aic,
        ),
        student_t=StudentTCopula(
            correlation=_matrix(fit.t_correlation),
            degrees_of_freedom=fit.degrees_of_freedom,
            degrees_of_freedom_at_bound=fit.degrees_of_freedom_at_bound,
            log_likelihood=fit.t_log_likelihood,
            aic=fit.t_aic,
        ),
        preferred="student_t" if t_preferred else "gaussian",
        aic_difference=abs(fit.gaussian_aic - fit.t_aic),
        pairs=[
            PairDependence(
                symbols=(fit.symbols[i], fit.symbols[j]),
                kendall_tau=float(fit.kendall_tau[i, j]),
                tail_dependence=float(tail[i, j]),
                empirical_lower_tail=float(fit.empirical_lower_tail[i, j]),
                empirical_upper_tail=float(fit.empirical_upper_tail[i, j]),
            )
            for i in range(fit.dimension)
            for j in range(i + 1, fit.dimension)
        ],
        left_out=left_out,
        warnings=warnings,
        notice=notice,
    )


@router.post(
    "/copula",
    response_model=CopulaFitResponse,
    summary="Fit Gaussian and Student t copulas to several assets",
)
async def create_copula_fit(request: Annotated[CopulaRequest, Body()]) -> CopulaFitResponse:
    """Model how ``symbols`` move together, separately from how each moves alone.

    Daily returns over ``period`` are aligned on the days every asset traded and turned
    into ranks. A Gaussian copula and a Student t copula are fitted to them and compared
    by AIC. The t copula's degrees of freedom measure how much more often the assets
    have extreme days together than a Gaussian copula allows; ``pairs`` gives each pair's
    implied tail dependence alongside how often it was observed.

    Assets the fit cannot use are left out and listed in ``left_out``: money-market
    funds, whose price holds at $1; those with too little history or whose history
    starts too late; and those whose prices cannot be fetched. Matrices are rows in the
    order of the response's ``symbols``, the assets kept. Returns 422 when fewer than two
    assets can be kept, and 502 when the price provider fails for every one.
    """
    usable = await _load_usable_returns(request.symbols, request.period)
    left_out = [
        *(LeftOutHolding(symbol=s, reason="cash", detail=d) for s, d in usable.cash.items()),
        *usable.left_out,
    ]
    if usable.panel.returns.shape[1] < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="At least two holdings need enough price history to compare; "
            f"{_left_out_text(left_out)}.",
        )
    fit = await asyncio.to_thread(fit_copulas, usable.panel.returns)
    order = {symbol: i for i, symbol in enumerate(request.symbols)}
    return _response(
        fit,
        usable.panel,
        request.period,
        _warnings(fit, usable.timezones),
        _window_notice(usable.panel, usable.closes, request.period),
        sorted(left_out, key=lambda holding: order[holding.symbol]),
    )


def _database_error(exc: SQLAlchemyError) -> HTTPException:
    """Log a database failure and translate it into a 503."""
    logger.error("Simulation database request failed: %s", exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="The simulation database is unavailable.",
    )


async def _load_returns(
    symbols: list[str], period: PortfolioPeriod
) -> tuple[ReturnPanel, pd.DataFrame, dict[str, str | None]]:
    """Download closes and align them into returns, translating failures to HTTP errors."""
    try:
        prices = await fetch_close_panel(symbols, period=period, interval="1d")
    except DataFetchError as exc:
        raise upstream_error(exc) from exc
    try:
        panel = return_panel(prices.closes)
    except InsufficientDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return panel, prices.closes, prices.timezones


CASH_DETAIL: str = "a money-market fund, whose price holds at $1"


def _left_out_text(left_out: list[LeftOutHolding]) -> str:
    """Name each holding left out and why, as one clause."""
    return "; ".join(f"{holding.symbol}: {holding.detail}" for holding in left_out)


@dataclass(frozen=True)
class UsableReturns:
    """The returns of the holdings a model can use, and what became of the rest."""

    panel: ReturnPanel
    closes: pd.DataFrame
    """Every close of the holdings kept, before they were aligned."""
    timezones: dict[str, str | None]
    cash: dict[str, str]
    """Holdings that act as cash, in the order given, with why."""
    left_out: list[LeftOutHolding]
    """Holdings that can be used neither as an asset nor as cash, in the order given."""


async def _quote_types(symbols: list[str]) -> dict[str, str | None]:
    """Each symbol's quote type, as its provider classes it; ``None`` when unknown."""
    found = await asyncio.gather(*(fetch_ticker_info(s) for s in symbols), return_exceptions=True)
    return {
        symbol: None if isinstance(info, BaseException) else info.quote_type
        for symbol, info in zip(symbols, found, strict=True)
    }


async def _load_usable_returns(symbols: list[str], period: PortfolioPeriod) -> UsableReturns:
    """Download closes and align the returns of the holdings that have enough of them.

    Money-market funds are recognized by ticker or quote type and returned as cash, as
    are holdings whose price never changes. Holdings with too little history, or whose
    prices cannot be fetched, are left out.

    Raises:
        HTTPException: 422 when no holding but cash can be used, and the statuses of
            :func:`upstream_error` when every download fails.
    """
    order = {symbol: i for i, symbol in enumerate(symbols)}
    cash = {symbol: CASH_DETAIL for symbol in symbols if is_money_market(symbol)}
    risky = [symbol for symbol in symbols if symbol not in cash]
    if not risky:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Every holding is a money-market fund, held as cash; there is nothing "
            "whose price moves to model.",
        )
    try:
        prices = await fetch_close_panel(
            risky, period=period, interval="1d", tolerate_failures=True
        )
    except DataFetchError as exc:
        raise upstream_error(exc) from exc
    try:
        panel, dropped = usable_return_panel(prices.closes)
    except InsufficientDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    left_out = [
        LeftOutHolding(
            symbol=symbol, reason="no_data", detail=f"its prices could not be fetched ({why})"
        )
        for symbol, why in prices.failed.items()
    ]
    left_out += [
        LeftOutHolding(symbol=d.symbol, reason=d.reason, detail=d.detail)
        for d in dropped
        if d.reason != "cash"
    ]
    cash |= {d.symbol: d.detail for d in dropped if d.reason == "cash"}
    # A fund the ticker does not mark as money market may still be one; ask the provider.
    quote_types = await _quote_types([holding.symbol for holding in left_out])
    for holding in list(left_out):
        if is_money_market(holding.symbol, quote_types.get(holding.symbol)):
            left_out.remove(holding)
            cash[holding.symbol] = CASH_DETAIL
    kept = [str(symbol) for symbol in panel.returns.columns]
    return UsableReturns(
        panel=panel,
        closes=prices.closes[kept],
        timezones={symbol: prices.timezones.get(symbol) for symbol in kept},
        cash=dict(sorted(cash.items(), key=lambda item: order[item[0]])),
        left_out=sorted(left_out, key=lambda holding: order[holding.symbol]),
    )


@router.post(
    "/simulate",
    response_model=SimulationResponse,
    summary="Monte Carlo simulation of a portfolio's value",
)
async def create_simulation(
    request: Annotated[SimulationRequest, Body()], session: Session, user: CurrentUser
) -> SimulationResponse:
    """Simulate a portfolio ``horizon`` trading days ahead.

    Daily returns over ``period`` are aligned on the days every holding traded. Each
    simulated day draws how the holdings move together from a ``student_t`` or
    ``gaussian`` copula fitted to them, or from resampled historical days
    (``empirical``), and each holding's own return from its history or a fitted Student
    t. With ``rebalancing`` ``daily`` the weights are reset every day; with ``none`` the
    portfolio is bought once and held, so weights drift. Returns the distribution of
    final value, expected return, probability of loss, VaR and CVaR at 95% and 99%,
    maximum drawdowns, daily percentiles for a fan chart, and ``tail_checks`` comparing
    how often holdings crash together in the simulation with history. Pass ``seed`` from
    a previous result to reproduce it.

    Money-market funds are simulated as cash, keeping their value, and listed in
    ``cash``. Holdings with too little history are left out and listed in ``left_out``;
    the rest are scaled up to make the whole portfolio, as ``simulated`` shows.

    The result is stored and its ``id`` returned for ``GET /portfolio/simulations/{id}``;
    if the database is unavailable it is still returned, with a null ``id``.

    Returns 422 for invalid or oversized settings or when no holding but cash has enough
    history, 502 when the price provider fails for every holding, and 503 when the
    server is busy with other simulations.
    """
    usable = await _load_usable_returns(request.symbols, request.period)
    panel = usable.panel
    kept = [str(symbol) for symbol in panel.returns.columns]
    included = sum(request.weights[symbol] for symbol in [*kept, *usable.cash])
    scaled = {
        symbol: weight / included
        for symbol, weight in request.weights.items()
        if symbol in kept or symbol in usable.cash
    }
    try:
        await asyncio.wait_for(_simulation_slots.acquire(), SIMULATION_QUEUE_SECONDS)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The server is busy with other simulations; try again shortly.",
            headers={"Retry-After": "10"},
        ) from exc
    try:
        summary = await asyncio.to_thread(
            simulate_portfolio,
            panel.returns,
            {symbol: scaled[symbol] for symbol in kept},
            horizon=request.horizon,
            paths=request.paths,
            dependence=request.dependence,
            marginals=request.marginals,
            initial_value=request.initial_value,
            seed=request.seed,
            rebalancing=request.rebalancing,
            cash={symbol: scaled[symbol] for symbol in usable.cash},
        )
    except SimulationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    finally:
        _simulation_slots.release()

    if asynchronous := asynchronous_trading_warning(usable.timezones):
        summary.warnings.append(asynchronous)
    response = SimulationResponse(
        holdings=request.holdings,
        period=request.period,
        start=panel.returns.index[0].date(),
        end=panel.returns.index[-1].date(),
        observations=panel.observations,
        excluded_dates=panel.excluded_dates,
        simulation=summary,
        simulated=[Holding(symbol=symbol, weight=weight) for symbol, weight in scaled.items()],
        cash=list(usable.cash),
        left_out=usable.left_out,
        notice=_window_notice(panel, usable.closes, request.period),
    )
    try:
        return await save_simulation(session, user.id, response)
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.error("Could not store the simulation: %s", exc)
        return response


@router.post(
    "/backtest",
    response_model=PortfolioBacktestResponse,
    summary="Backtest a strategy on every holding of a portfolio",
)
async def create_portfolio_backtest(
    request: Annotated[PortfolioBacktestRequest, Body()],
) -> PortfolioBacktestResponse:
    """Run ``strategy`` on each holding and combine the holdings at their weights.

    Daily closes are aligned on the days every holding traded, as for ``/simulate``. Each
    holding's position is set by the strategy on its own prices and scaled by its weight,
    and weights are reset every day. Trades pay ``cost_bps`` per unit of turnover;
    capital not invested earns nothing, or the risk-free rate with ``cash_interest``.
    The benchmark holds the same weights, rebalanced daily, without costs.

    ``assets`` gives each holding's contribution, and ``attribution`` regresses the
    portfolio's daily returns on a factor model to separate alpha from factor exposure.
    Results are not stored.

    Returns 404 naming unknown symbols, 422 for too little shared history for the
    strategy or an unknown dual momentum benchmark, and 502 when the price provider
    fails, or the risk-free rate or dual momentum's benchmark cannot be fetched.
    """
    (panel, closes, timezones), (factors, factor_notice), benchmark = await asyncio.gather(
        _load_returns(request.symbols, request.period),
        load_factors(request.attribution, request.cash_interest),
        load_benchmark(request.strategy, request.period, "1d"),
    )
    cash = (
        cash_returns(factors[RISK_FREE], panel.closes.index)
        if request.cash_interest and factors is not None
        else None
    )
    try:
        result = await asyncio.to_thread(
            run_portfolio_backtest,
            panel.closes,
            request.weights,
            strategy_for(request.strategy, 252, benchmark),
            252,
            cost_bps=request.cost_bps,
            risk_free_rate=request.risk_free_rate,
            cash=cash,
        )
    except InsufficientDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    attribution, attribution_notice = attribute(result.returns, factors, request.attribution)
    notices = [
        _window_notice(panel, closes, request.period),
        asynchronous_trading_warning(timezones),
        factor_notice,
        attribution_notice,
    ]
    return PortfolioBacktestResponse(
        holdings=request.holdings,
        period=request.period,
        strategy=request.strategy,
        cost_bps=request.cost_bps,
        risk_free_rate=request.risk_free_rate,
        cash_interest=request.cash_interest,
        observations=panel.observations,
        excluded_dates=panel.excluded_dates,
        metrics=result.metrics,
        benchmark=result.benchmark,
        trades=result.trades,
        exposure=result.exposure,
        assets=[
            AssetBacktest(
                symbol=symbol,
                weight=part.weight,
                contribution=part.contribution,
                exposure=part.exposure,
                trades=part.trades,
            )
            for symbol, part in result.assets.items()
        ],
        attribution=attribution,
        equity_curve=equity_points(result),
        notice=" ".join(notice for notice in notices if notice) or None,
    )


@router.get("/simulations", response_model=SimulationList, summary="List stored simulations")
async def read_simulations(
    session: Session,
    user: CurrentUser,
    symbol: Annotated[
        str | None,
        Query(pattern=SYMBOL_PATTERN, description="Only simulations holding this symbol."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SimulationList:
    """Return stored simulations' headline figures, newest first.

    Returns 503 when the database is unavailable.
    """
    try:
        return await list_simulations(session, user.id, symbol, limit, offset)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@router.get(
    "/simulations/{simulation_id}",
    response_model=SimulationResponse,
    summary="A stored simulation in full",
)
async def read_simulation(
    simulation_id: UUID, session: Session, user: CurrentUser
) -> SimulationResponse:
    """Return a stored simulation with its distributions and fan chart.

    Returns 404 for unknown IDs and other users' simulations, and 503 when the database
    is unavailable.
    """
    try:
        result = await get_simulation(session, user.id, simulation_id)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No simulation with ID {simulation_id}.",
        )
    return result


@router.delete(
    "/simulations/{simulation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a stored simulation",
)
async def remove_simulation(simulation_id: UUID, session: Session, user: CurrentUser) -> Response:
    """Delete a stored simulation.

    Returns 404 for unknown IDs and other users' simulations, and 503 when the database
    is unavailable.
    """
    try:
        deleted = await delete_simulation(session, user.id, simulation_id)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No simulation with ID {simulation_id}.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _value_holdings(
    holdings: list[HoldingShares], portfolio: int = 1, name: str | None = None
) -> HoldingsResponse:
    """Price ``holdings`` concurrently and weight them by value.

    A money-market fund without a price is valued at the $1 a share it holds.
    """
    results = await asyncio.gather(
        *(fetch_ticker_info(holding.symbol) for holding in holdings), return_exceptions=True
    )
    valued: list[HoldingValue] = []
    failures: list[str] = []
    for holding, result in zip(holdings, results, strict=True):
        if isinstance(result, DataFetchError):
            if is_money_market(holding.symbol):
                valued.append(
                    HoldingValue(
                        symbol=holding.symbol,
                        shares=holding.shares,
                        price=MONEY_MARKET_PRICE,
                        value=holding.shares * MONEY_MARKET_PRICE,
                        cash=True,
                    )
                )
                continue
            failures.append(f"{holding.symbol} ({result})")
            valued.append(HoldingValue(symbol=holding.symbol, shares=holding.shares))
            continue
        if isinstance(result, BaseException):
            raise result
        cash = is_money_market(holding.symbol, result.quote_type)
        price = MONEY_MARKET_PRICE if cash and not result.price else result.price
        value = None if price is None else holding.shares * price
        if value is None:
            failures.append(f"{holding.symbol} (no price)")
        valued.append(
            HoldingValue(
                symbol=holding.symbol,
                shares=holding.shares,
                name=result.name,
                sector=result.sector,
                industry=result.industry,
                price=price,
                value=value,
                cash=cash,
            )
        )
    total = sum(position.value for position in valued if position.value is not None)
    for position in valued:
        if position.value is not None and total > 0:
            position.weight = position.value / total
    return HoldingsResponse(
        portfolio=portfolio,
        name=name or default_portfolio_name(portfolio),
        holdings=valued,
        total_value=total,
        priced_at=datetime.now(timezone.utc),
        notice=f"Could not price {'; '.join(failures)}." if failures else None,
    )


@router.get("/portfolios", response_model=PortfolioList, summary="Your saved portfolios")
async def read_portfolios(session: Session, user: CurrentUser) -> PortfolioList:
    """List both of the portfolios you can save, with their names and how many holdings
    each has; one never saved has none. Returns 503 when the database is unavailable."""
    try:
        return PortfolioList(portfolios=await list_portfolios(session, user.id))
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@router.get("/holdings", response_model=HoldingsResponse, summary="One of your saved portfolios")
async def read_holdings(
    session: Session, user: CurrentUser, portfolio: PortfolioQuery = 1
) -> HoldingsResponse:
    """Return a saved portfolio's holdings valued at the latest prices, with their weights.

    Holdings that cannot be priced are listed without a value or weight and named in
    ``notice``. Returns 503 when the database is unavailable.
    """
    try:
        holdings = await get_holdings(session, user.id, portfolio)
        name = await get_portfolio_name(session, user.id, portfolio)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    return await _value_holdings(holdings, portfolio, name)


@router.put("/holdings", response_model=HoldingsResponse, summary="Save one of your portfolios")
async def store_holdings(
    session: Session,
    user: CurrentUser,
    update: Annotated[HoldingsUpdate, Body()],
    portfolio: PortfolioQuery = 1,
) -> HoldingsResponse:
    """Replace a saved portfolio's holdings (up to 40 symbols), and its name when
    ``name`` is given, and return them valued.

    Simulate or fit a copula to them by sending their ``weight`` values to
    ``/portfolio/simulate`` or their symbols to ``/portfolio/copula``.
    """
    try:
        await replace_holdings(session, user.id, update.holdings, portfolio, update.name)
        name = await get_portfolio_name(session, user.id, portfolio)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    return await _value_holdings(update.holdings, portfolio, name)


# Claude's review of the saved portfolio.

MAX_PLANNED_POSITIONS: int = 50
"""Most target positions a plan covers."""

CORRELATED_PAIRS: int = 5
"""Most and least correlated pairs listed in a review's data."""


async def _portfolio_context(
    holdings: list[HoldingShares], period: PortfolioPeriod, profile: InvestorProfile
) -> PortfolioContext:
    """Value the holdings and compute the statistics a review is written from.

    Raises:
        HTTPException: 422 when no holding can be priced or the window is too short, and
            the statuses of :func:`upstream_error` when prices cannot be fetched.
    """
    valued = await _value_holdings(holdings)
    priced = [h for h in valued.holdings if h.value is not None and h.value > 0]
    if not priced:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"None of your holdings could be priced. {valued.notice or ''}".strip(),
        )
    usable = await _load_usable_returns([h.symbol for h in priced], period)
    panel = usable.panel
    # Cash earns nothing in these statistics: a column of zero returns.
    returns = panel.returns.assign(**{symbol: 0.0 for symbol in usable.cash})
    reviewed = [h for h in priced if h.symbol in returns.columns]
    left_out = [h.symbol for h in valued.holdings if h not in reviewed]
    total = sum(h.value for h in reviewed if h.value is not None)
    weights = pd.Series({h.symbol: (h.value or 0.0) / total for h in reviewed})
    stats = portfolio_stats(returns[weights.index], weights)
    most, least = ranked_pairs(stats.correlations, CORRELATED_PAIRS)
    sectors: dict[str, float] = {}
    for h in reviewed:
        sector = "Cash" if h.symbol in usable.cash else h.sector or "Unclassified"
        sectors[sector] = sectors.get(sector, 0.0) + float(weights[h.symbol])
    notices = [
        notice
        for notice in (
            _window_notice(panel, usable.closes, period),
            f"Left out of the review: {valued.notice}" if valued.notice else None,
            (
                f"Left out for too little price history: {_left_out_text(usable.left_out)}."
                if usable.left_out
                else None
            ),
            (
                f"Counted as cash: {', '.join(usable.cash)}, money-market funds earning "
                "nothing in these statistics."
                if usable.cash
                else None
            ),
        )
        if notice
    ]
    return PortfolioContext(
        profile=profile,
        period=period,
        total_value=total,
        holdings=[
            AssetRisk(
                symbol=h.symbol,
                name=h.name,
                sector=h.sector,
                industry=h.industry,
                shares=h.shares,
                price=h.price or 0.0,
                value=h.value or 0.0,
                weight=float(weights[h.symbol]),
                cash=h.symbol in usable.cash,
                **vars(stats.assets[h.symbol]),
            )
            for h in reviewed
        ],
        sectors=dict(sorted(sectors.items(), key=lambda item: -item[1])),
        portfolio=PortfolioRiskStats(
            start=stats.start.to_pydatetime(),
            end=stats.end.to_pydatetime(),
            observations=stats.observations,
            annualized_return=stats.annualized_return,
            volatility=stats.volatility,
            max_drawdown=stats.max_drawdown,
            current_drawdown=stats.current_drawdown,
            return_to_volatility=(
                stats.annualized_return / stats.volatility if stats.volatility > 0 else None
            ),
            diversification_ratio=stats.diversification_ratio,
            effective_holdings=stats.effective_holdings,
            largest_weight=float(weights.max()),
            average_correlation=stats.average_correlation,
        ),
        most_correlated=[PairCorrelation(first=a, second=b, correlation=c) for a, b, c in most],
        least_correlated=[PairCorrelation(first=a, second=b, correlation=c) for a, b, c in least],
        left_out=left_out,
        notice=" ".join(notices) or None,
    )


@dataclass(frozen=True)
class PreparedReview:
    """What a portfolio review needs before Claude is asked: or a recent one to reuse."""

    user_id: str
    request: PortfolioAnalysisRequest
    key: str
    context: PortfolioContext | None = None
    api_key: SecretStr | None = None
    cached: PortfolioAnalysisResponse | None = None

    def response(
        self, report: PortfolioReview, model: str, plan: RebalancePlan | None
    ) -> PortfolioAnalysisResponse:
        assert self.context is not None
        return PortfolioAnalysisResponse(
            period=self.request.period,
            report=report,
            plan=plan,
            context=self.context,
            model=model,
            generated_at=datetime.now(timezone.utc),
        )


async def prepare_portfolio_review(
    user: CurrentUser,
    request: Annotated[PortfolioAnalysisRequest, Body()] = PortfolioAnalysisRequest(),
) -> PreparedReview:
    """Load the saved holdings, then reuse a recent review of them or gather the data for
    a new one. Runs as a dependency, so a streamed review fails with an HTTP status before
    its stream opens."""
    settings = get_settings()
    try:
        async with get_sessionmaker()() as session:
            holdings = await get_holdings(session, user.id, request.portfolio)
            key = holdings_key(holdings, request.profile.model_dump_json())
            cached = None
            if holdings and not request.refresh and settings.analysis_cache_ttl_seconds > 0:
                cached = await find_recent_portfolio_analysis(
                    session,
                    user.id,
                    request.period,
                    key,
                    settings.anthropic_model,
                    settings.anthropic_effort,
                    datetime.now(timezone.utc)
                    - timedelta(seconds=settings.analysis_cache_ttl_seconds),
                    request.portfolio,
                )
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    if not holdings:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Save your holdings first; the review is of your saved portfolio.",
        )
    if cached is not None:
        return PreparedReview(
            user_id=user.id,
            request=request,
            key=key,
            cached=cached.model_copy(update={"cached": True}),
        )
    api_key = anthropic_key()
    await check_ai_limits(user)
    context = await _portfolio_context(holdings, request.period, request.profile)
    return PreparedReview(
        user_id=user.id, request=request, key=key, context=context, api_key=api_key
    )


PreparedPortfolioReview = Annotated[PreparedReview, Depends(prepare_portfolio_review)]


async def _plan(context: PortfolioContext, report: PortfolioReview) -> RebalancePlan | None:
    """The orders that carry out the review's target allocation, sized at latest prices.

    New symbols are priced here; one that cannot be is planned by value alone. Returns
    ``None`` when the review gives no targets.
    """
    # Targets come from Claude, or from an answer pasted from outside the app: only real
    # ticker symbols are planned, and few enough to price, since each new one is looked up.
    targets = {
        symbol: Target(
            position.target_weight,
            position.order_type,
            position.limit_offset_percent,
            position.rationale,
        )
        for position in report.target_allocation[:MAX_PLANNED_POSITIONS]
        if re.fullmatch(SYMBOL_PATTERN, symbol := position.symbol.strip().upper())
    }
    if not targets:
        return None
    held = {h.symbol: (h.shares, h.price) for h in context.holdings if h.price > 0}
    new = [s for s in targets if s not in held and s != CASH]
    found = await asyncio.gather(*(fetch_ticker_info(s) for s in new), return_exceptions=True)
    prices = {
        symbol: None if isinstance(info, BaseException) else info.price
        for symbol, info in zip(new, found, strict=True)
    }
    plan = plan_trades(held, targets, prices, context.profile.cash_to_invest)
    return RebalancePlan(
        total_value=plan.total_value,
        trades=[PlannedTrade(**vars(trade)) for trade in plan.trades],
        cash_after=plan.cash_after,
        notes=plan.notes,
    )


async def _remember(
    prepared: PreparedReview, response: PortfolioAnalysisResponse
) -> PortfolioAnalysisResponse:
    """Store a new review; if the database fails, return it unsaved rather than lose it."""
    settings = get_settings()
    try:
        async with get_sessionmaker()() as session:
            return await save_portfolio_analysis(
                session,
                prepared.user_id,
                prepared.key,
                response,
                settings.anthropic_model,
                settings.anthropic_effort,
                prepared.request.portfolio,
            )
    except SQLAlchemyError as exc:
        logger.error("Could not store a portfolio review: %s", exc)
        return response


@router.post(
    "/analysis",
    response_model=PortfolioAnalysisResponse,
    summary="AI-written review of your saved portfolio",
)
async def create_portfolio_review(prepared: PreparedPortfolioReview) -> PortfolioAnalysisResponse:
    """Have Claude review your saved portfolio's construction and risk.

    The review is grounded in your holdings valued at the latest prices, their sectors,
    and statistics of today's weights over ``period`` of daily returns: each holding's
    return, volatility, drawdown and share of the portfolio's risk, the portfolio's
    volatility, drawdowns and diversification, and the most and least correlated pairs.
    All of it is in ``context``.

    It is written with, and billed to, your own Anthropic key (the ``X-Anthropic-Key``
    header), within your AI limits, and stored under your account. A review of the same
    holdings and period within ``ANALYSIS_CACHE_TTL_SECONDS`` is returned again with
    ``cached`` set and no charge, unless ``refresh`` is true.

    Returns 403 without a valid Anthropic key, 422 with no saved holdings, a window too
    short, or when the model declines, 429 beyond your AI limits, 502 when a provider
    fails, and 503 when Anthropic rate limits your account.
    """
    if prepared.cached is not None:
        return prepared.cached
    assert prepared.api_key is not None and prepared.context is not None
    try:
        async with anthropic_client(prepared.api_key) as client:
            report, model = await write_portfolio_review(prepared.context, client=client)
    except AnalysisError as exc:
        raise ai_error(exc) from exc
    plan = await _plan(prepared.context, report)
    return await _remember(prepared, prepared.response(report, model, plan))


@router.post(
    "/analysis/stream",
    response_class=EventSourceResponse,
    summary="Stream an AI-written review of your saved portfolio as Server-Sent Events",
)
async def stream_portfolio_review_events(
    prepared: PreparedPortfolioReview,
) -> AsyncIterator[ServerSentEvent]:
    """Stream the review of ``create_portfolio_review`` as Claude writes it.

    The same failures return the same statuses before the stream opens. Then it sends
    ``context`` (the :class:`PortfolioContext`), ``thinking`` and ``report`` fragments,
    ``fallback`` when a model declines part-way and another continues, and ``result``
    (the :class:`PortfolioAnalysisResponse`) or ``error``, as the stock analysis stream
    does. A reused review is sent as ``context`` then ``result`` alone.
    """
    if prepared.cached is not None:
        yield ServerSentEvent(event="context", data=prepared.cached.context)
        yield ServerSentEvent(event="result", data=prepared.cached)
        return
    assert prepared.api_key is not None and prepared.context is not None
    yield ServerSentEvent(event="context", data=prepared.context)
    try:
        async with anthropic_client(prepared.api_key) as client:
            async for item in stream_portfolio_review(prepared.context, client=client):
                if isinstance(item, Written):
                    plan = await _plan(prepared.context, item.output)
                    response = await _remember(
                        prepared, prepared.response(item.output, item.model, plan)
                    )
                    yield ServerSentEvent(event="result", data=response)
                elif isinstance(item, AnalysisDelta):
                    yield ServerSentEvent(event=item.channel, data=item)
                else:
                    yield ServerSentEvent(event="fallback", data=item)
    except AnalysisError as exc:
        error = ai_error(exc)
        yield ServerSentEvent(
            event="error",
            data=AnalysisStreamError(
                status=error.status_code,
                detail=str(error.detail),
                retry_after=exc.retry_after if isinstance(exc, AIRateLimitError) else None,
            ),
        )


@router.get(
    "/analysis",
    response_model=PortfolioAnalysisResponse,
    summary="Your latest portfolio review",
)
async def read_portfolio_review(
    session: Session, user: CurrentUser, portfolio: PortfolioQuery = 1
) -> PortfolioAnalysisResponse:
    """Return your newest stored review of ``portfolio``, whatever it was written for, so
    it can be shown again without a charge. Returns 404 when you have none."""
    try:
        review = await latest_portfolio_analysis(session, user.id, portfolio)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No portfolio review yet."
        )
    return review.model_copy(update={"cached": True})


async def _saved_holdings(
    session: AsyncSession, user_id: str, portfolio: int
) -> list[HoldingShares]:
    """A saved portfolio's holdings; 422 when there are none, 503 when unreadable."""
    try:
        holdings = await get_holdings(session, user_id, portfolio)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    if not holdings:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Save your holdings first; the review is of your saved portfolio.",
        )
    return holdings


@router.post(
    "/analysis/prompt",
    response_model=PortfolioPromptExport,
    summary="The portfolio review prompt, to paste into Claude outside the app",
)
async def export_portfolio_prompt(
    session: Session,
    user: CurrentUser,
    request: Annotated[PortfolioAnalysisRequest, Body()] = PortfolioAnalysisRequest(),
) -> PortfolioPromptExport:
    """Return exactly what a review of your saved portfolio would send Claude.

    No Anthropic key is needed and nothing counts against your AI limits. Paste
    ``prompt`` into Claude, then send its answer to ``/analysis/import`` with the
    ``context`` returned here.
    """
    holdings = await _saved_holdings(session, user.id, request.portfolio)
    context = await _portfolio_context(holdings, request.period, request.profile)
    return PortfolioPromptExport(
        prompt=render_prompt(PORTFOLIO_SYSTEM_PROMPT, review_messages(context), PortfolioReview),
        filename=f"pyqxel-portfolio-review-{request.period}-prompt.md",
        context=context,
    )


@router.post(
    "/analysis/import",
    response_model=PortfolioAnalysisResponse,
    summary="Save a portfolio review Claude wrote outside the app",
)
async def import_portfolio_review(
    session: Session, user: CurrentUser, body: Annotated[PortfolioReviewImport, Body()]
) -> PortfolioAnalysisResponse:
    """Check Claude's answer to an exported prompt and store it as your latest review.

    Its target allocation is turned into orders at the latest prices, as for reviews the
    app writes. It is marked as pasted and never reused in place of a new review.
    Returns 422 when the answer does not match the review's format.
    """
    try:
        report = parse_response(body.response, PortfolioReview)
    except ExternalResponseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    holdings = await _saved_holdings(session, user.id, body.request.portfolio)
    plan = await _plan(body.context, report)
    response = PortfolioAnalysisResponse(
        period=body.context.period,
        report=report,
        plan=plan,
        context=body.context,
        model=PASTED_MODEL,
        generated_at=datetime.now(timezone.utc),
    )
    try:
        return await save_portfolio_analysis(
            session,
            user.id,
            holdings_key(holdings, body.request.profile.model_dump_json()),
            response,
            PASTED_MODEL,
            PASTED_MODEL,
            body.request.portfolio,
        )
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
