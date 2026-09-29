"""Schemas describing volatility model estimates."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.models.stock import HistoryCoverage

GarchDistribution = Literal["norm", "std"]
"""Innovation distributions: Gaussian (``norm``) or Student t (``std``)."""

VolatilityModel = Literal["garch", "ewma"]
"""Volatility models: GARCH(1,1) estimated in R, or RiskMetrics EWMA."""

VolatilityPeriod = Literal["6mo", "1y", "2y", "5y", "10y", "max"]
"""Lookback windows. GARCH needs ``2y`` or more of daily bars; EWMA accepts all."""

VolatilityInterval = Literal["1d", "1wk"]
"""Bar sizes with a fixed number of periods per year and enough bars to model."""


class GarchParameter(BaseModel):
    """A fitted GARCH coefficient.

    ``mu`` and ``omega`` are in percent-return units (``omega`` in percent squared);
    ``alpha1``, ``beta1`` and ``shape`` are unitless.
    """

    name: str
    estimate: float
    std_error: float | None = Field(
        default=None, description="Robust (QML) standard error; null when not estimable."
    )


class VolatilityPoint(BaseModel):
    """Annualized in-sample conditional volatility at the close of one bar."""

    timestamp: datetime
    volatility: float = Field(ge=0)


class VolatilityForecastStep(BaseModel):
    """Annualized conditional volatility forecast ``step`` bars after the last close."""

    step: int = Field(ge=1)
    volatility: float = Field(ge=0)


class GarchFit(BaseModel):
    """A constant-mean GARCH(1,1) fit to log returns.

    Volatilities are annualized decimals (0.25 = 25%).
    """

    model: Literal["garch"] = "garch"
    distribution: GarchDistribution
    observations: int = Field(ge=1, description="Number of returns the model was fit to.")
    parameters: list[GarchParameter]
    persistence: float = Field(description="``alpha1 + beta1``; shocks decay slower near 1.")
    half_life: float | None = Field(
        default=None,
        description="Bars for a volatility shock to decay by half; null when persistence >= 1.",
    )
    current_volatility: float = Field(ge=0, description="Conditional volatility at the last bar.")
    long_run_volatility: float | None = Field(
        default=None,
        ge=0,
        description="Level forecasts revert to; null when persistence >= 1.",
    )
    realized_volatility: float = Field(ge=0, description="Sample volatility of the same returns.")
    conditional_volatility: list[VolatilityPoint]
    forecast: list[VolatilityForecastStep]
    log_likelihood: float
    aic: float = Field(description="Akaike information criterion, per observation.")
    bic: float = Field(description="Bayesian information criterion, per observation.")
    warnings: list[str] = Field(
        default_factory=list,
        description="Reasons the estimates may be unreliable; empty for a well-behaved fit.",
    )


class EwmaFit(BaseModel):
    """A RiskMetrics EWMA volatility estimate with zero-mean returns.

    Volatilities are annualized decimals (0.25 = 25%). EWMA does not mean-revert, so
    every forecast step equals the one-step-ahead volatility.
    """

    model: Literal["ewma"] = "ewma"
    decay: float = Field(gt=0, lt=1, description="Weight kept by the previous variance.")
    observations: int = Field(ge=1, description="Number of returns used.")
    half_life: float = Field(gt=0, description="Bars for a return's weight to halve.")
    current_volatility: float = Field(ge=0, description="Conditional volatility at the last bar.")
    realized_volatility: float = Field(ge=0, description="Sample volatility of the same returns.")
    conditional_volatility: list[VolatilityPoint]
    forecast: list[VolatilityForecastStep]
    warnings: list[str] = Field(
        default_factory=list,
        description="Reasons the estimate may be unreliable; empty when it is not.",
    )


VolatilityFit = Annotated[GarchFit | EwmaFit, Field(discriminator="model")]
"""A fit from either volatility model, tagged by ``model``."""


class VolatilityEstimate(BaseModel):
    """Volatility estimate for a ticker over a lookback window."""

    symbol: str
    period: VolatilityPeriod
    interval: VolatilityInterval
    periods_per_year: int = Field(description="Bars per year used to annualize.")
    fit: VolatilityFit
    coverage: HistoryCoverage = Field(
        description="``full`` when bars span the window, ``partial`` when data begins after "
        "the window starts."
    )
    notice: str | None = Field(
        default=None, description="Explains which part of the window has no data, if any."
    )
