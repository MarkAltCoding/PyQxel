"""Multi-asset routes: how a set of assets moves together, and simulated portfolio outcomes."""

import asyncio
import logging
from typing import Annotated
from uuid import UUID

import numpy as np
import pandas as pd
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.backtest import attribute, equity_points, load_factors
from app.api.v1.endpoints.stocks import COVERAGE_TOLERANCE, PERIOD_OFFSETS, upstream_error
from app.data.fetcher import DataFetchError
from app.data.panel import fetch_close_panel
from app.db.session import get_session
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
    Matrix,
    PairDependence,
    PortfolioPeriod,
    StudentTCopula,
)
from app.models.backtest import (
    AssetBacktest,
    PortfolioBacktestRequest,
    PortfolioBacktestResponse,
)
from app.models.factors import RISK_FREE
from app.models.simulation import SimulationList, SimulationRequest, SimulationResponse
from app.models.stock import SYMBOL_PATTERN
from app.stats.backtest import cash_returns, run_portfolio_backtest, strategy_for
from app.stats.copulas import CopulaFit, asynchronous_trading_warning, fit_copulas
from app.stats.monte_carlo import SimulationError, simulate_portfolio
from app.stats.panel import ReturnPanel, first_date, return_panel
from app.stats.volatility import InsufficientDataError

logger = logging.getLogger(__name__)

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]

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

    Matrices are rows in the order of ``symbols``. Returns 404 naming unknown symbols, 422
    when the assets share too little history, and 502 when the price provider fails.
    """
    panel, closes, timezones = await _load_returns(request.symbols, request.period)
    fit = await asyncio.to_thread(fit_copulas, panel.returns)
    return _response(
        fit,
        panel,
        request.period,
        _warnings(fit, timezones),
        _window_notice(panel, closes, request.period),
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


@router.post(
    "/simulate",
    response_model=SimulationResponse,
    summary="Monte Carlo simulation of a portfolio's value",
)
async def create_simulation(
    request: Annotated[SimulationRequest, Body()], session: Session
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

    The result is stored and its ``id`` returned for ``GET /portfolio/simulations/{id}``;
    if the database is unavailable it is still returned, with a null ``id``.

    Returns 404 naming unknown symbols, 422 for invalid or oversized settings or too
    little shared history, 502 when the price provider fails, and 503 when the server is
    busy with other simulations.
    """
    panel, closes, timezones = await _load_returns(request.symbols, request.period)
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
            request.weights,
            horizon=request.horizon,
            paths=request.paths,
            dependence=request.dependence,
            marginals=request.marginals,
            initial_value=request.initial_value,
            seed=request.seed,
            rebalancing=request.rebalancing,
        )
    except SimulationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    finally:
        _simulation_slots.release()

    if asynchronous := asynchronous_trading_warning(timezones):
        summary.warnings.append(asynchronous)
    response = SimulationResponse(
        holdings=request.holdings,
        period=request.period,
        start=panel.returns.index[0].date(),
        end=panel.returns.index[-1].date(),
        observations=panel.observations,
        excluded_dates=panel.excluded_dates,
        simulation=summary,
        notice=_window_notice(panel, closes, request.period),
    )
    try:
        return await save_simulation(session, response)
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
    strategy, and 502 when the price provider fails, or the risk-free rate cannot be
    fetched for ``cash_interest``.
    """
    (panel, closes, timezones), (factors, factor_notice) = await asyncio.gather(
        _load_returns(request.symbols, request.period),
        load_factors(request.attribution, request.cash_interest),
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
            strategy_for(request.strategy),
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
        return await list_simulations(session, symbol, limit, offset)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@router.get(
    "/simulations/{simulation_id}",
    response_model=SimulationResponse,
    summary="A stored simulation in full",
)
async def read_simulation(simulation_id: UUID, session: Session) -> SimulationResponse:
    """Return a stored simulation with its distributions and fan chart.

    Returns 404 for unknown IDs and 503 when the database is unavailable.
    """
    try:
        result = await get_simulation(session, simulation_id)
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
async def remove_simulation(simulation_id: UUID, session: Session) -> Response:
    """Delete a stored simulation.

    Returns 404 for unknown IDs and 503 when the database is unavailable.
    """
    try:
        deleted = await delete_simulation(session, simulation_id)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No simulation with ID {simulation_id}.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
