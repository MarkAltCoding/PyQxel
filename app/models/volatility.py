"""Schemas describing volatility model estimates."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.stock import HistoryCoverage

GarchDistribution = Literal["norm", "std"]
"""Innovation distributions: Gaussian (``norm``) or Student t (``std``)."""

VolatilityPeriod = Literal["2y", "5y", "10y", "max"]
"""Lookback windows long enough to estimate a GARCH model."""

VolatilityInterval = Literal["1d", "1wk"]
"""Bar sizes with a fixed number of periods per year and enough bars to fit a GARCH model."""


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
    """A GARCH(1,1) fit to log returns. Volatilities are annualized decimals (0.25 = 25%)."""

    model: Literal["sGARCH(1,1)"] = "sGARCH(1,1)"
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


class VolatilityEstimate(BaseModel):
    """GARCH volatility estimate for a ticker over a lookback window."""

    symbol: str
    period: VolatilityPeriod
    interval: VolatilityInterval
    periods_per_year: int = Field(description="Bars per year used to annualize.")
    garch: GarchFit
    coverage: HistoryCoverage = Field(
        description="``full`` when bars span the window, ``partial`` when data begins after "
        "the window starts."
    )
    notice: str | None = Field(
        default=None, description="Explains which part of the window has no data, if any."
    )
