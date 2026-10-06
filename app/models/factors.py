"""Factor models, the Fama-French factors they are built from, and regression results."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.models.stock import HistoryCoverage

FactorModel = Literal["ff3", "carhart4", "ff5"]
"""Fama-French three-factor, Carhart four-factor (three plus momentum), Fama-French five-factor."""

FactorName = Literal["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"]
"""Factor returns as Ken French names them: market excess return, size, value,
profitability, investment and momentum."""

RISK_FREE: str = "RF"
"""Column holding the daily risk-free rate, the one-month T-bill compounded daily."""

MODEL_FACTORS: dict[FactorModel, list[FactorName]] = {
    "ff3": ["Mkt-RF", "SMB", "HML"],
    "carhart4": ["Mkt-RF", "SMB", "HML", "Mom"],
    "ff5": ["Mkt-RF", "SMB", "HML", "RMW", "CMA"],
}
"""The factors each model regresses on, in the order they are reported."""

FactorPeriod = Literal["1y", "2y", "5y", "10y"]
"""Lookback windows of daily bars a factor regression may cover."""


class Coefficient(BaseModel):
    """An estimate with Newey-West (HAC) standard error, t-statistic and p-value."""

    estimate: float
    std_error: float = Field(ge=0)
    t_stat: float | None = Field(description="Null when the standard error is zero.")
    p_value: float | None = Field(
        ge=0, le=1, description="Two-sided; null when the standard error is zero."
    )


class FactorExposure(Coefficient):
    """The stock's sensitivity (beta) to one factor.

    ``estimate`` is the beta: the excess return expected from a one-unit factor return.
    """

    factor: FactorName
    variance_share: float = Field(
        description="Share of the stock's return variance attributed to this factor, "
        "beta times its covariance with the stock over the stock's variance. Shares sum "
        "to R-squared; one can be negative when factors are correlated."
    )


class FactorFit(BaseModel):
    """An ordinary least squares regression of daily excess returns on factor returns.

    Returns and volatilities are decimals (0.25 = 25%), annualized over 252 trading days.
    """

    start: date = Field(description="First day whose return is in the regression.")
    end: date = Field(description="Last day whose return is in the regression.")
    observations: int = Field(ge=1)
    alpha: Coefficient = Field(
        description="Annualized intercept: the excess return not explained by the factors."
    )
    exposures: list[FactorExposure]
    r_squared: float = Field(ge=0, le=1)
    adjusted_r_squared: float = Field(le=1)
    total_volatility: float = Field(ge=0, description="Volatility of the excess returns.")
    residual_volatility: float = Field(
        ge=0, description="Idiosyncratic volatility: of the returns the factors do not explain."
    )
    systematic_share: float = Field(
        ge=0, le=1, description="Share of return variance the factors explain (R-squared)."
    )
    idiosyncratic_share: float = Field(ge=0, le=1, description="The rest: 1 - R-squared.")
    hac_lags: int = Field(ge=0, description="Lags in the Newey-West standard errors.")
    warnings: list[str] = Field(default_factory=list)


class FactorRegression(BaseModel):
    """A stock's exposures to a Fama-French factor model."""

    symbol: str
    model: FactorModel
    period: FactorPeriod
    fit: FactorFit
    factor_data_end: date = Field(
        description="Last date of the published factor data; later returns are left out."
    )
    coverage: HistoryCoverage
    notice: str | None = Field(
        default=None, description="Explains any part of the window without data."
    )
    source: str = (
        "Factor returns from the Kenneth R. French Data Library "
        "(mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html)."
    )


class FactorContext(BaseModel):
    """Exposures to a factor model, estimated over a window of daily returns."""

    model: FactorModel
    factor_data_end: date = Field(
        description="Last date of the published factor data; the regression stops there."
    )
    fit: FactorFit
