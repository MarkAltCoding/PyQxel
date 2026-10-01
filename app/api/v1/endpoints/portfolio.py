"""Multi-asset routes: how a set of assets moves together."""

import asyncio
from typing import Annotated

import numpy as np
import pandas as pd
from fastapi import APIRouter, Body, HTTPException, status

from app.api.v1.endpoints.stocks import COVERAGE_TOLERANCE, PERIOD_OFFSETS, upstream_error
from app.data.fetcher import DataFetchError
from app.data.panel import fetch_close_panel
from app.models.portfolio import (
    CopulaFitResponse,
    CopulaRequest,
    GaussianCopula,
    Matrix,
    PairDependence,
    PortfolioPeriod,
    StudentTCopula,
)
from app.stats.copulas import CopulaFit, asynchronous_trading_warning, fit_copulas
from app.stats.panel import ReturnPanel, return_panel
from app.stats.volatility import InsufficientDataError

router = APIRouter()

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
    latest = max(closes.columns, key=lambda symbol: closes[symbol].first_valid_index())
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
    try:
        prices = await fetch_close_panel(request.symbols, period=request.period, interval="1d")
    except DataFetchError as exc:
        raise upstream_error(exc) from exc
    try:
        panel = return_panel(prices.closes)
    except InsufficientDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    fit = await asyncio.to_thread(fit_copulas, panel.returns)
    return _response(
        fit,
        panel,
        request.period,
        _warnings(fit, prices.timezones),
        _window_notice(panel, prices.closes, request.period),
    )
