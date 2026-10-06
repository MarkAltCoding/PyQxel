"""Multi-factor regression of a stock's daily excess returns on Fama-French factors.

Fama-French factors are simple daily returns, so the stock's returns are simple
returns too, less the same day's risk-free rate. Days are matched by calendar date;
a day missing from either series is left out rather than filled.

The fit is ordinary least squares. Daily returns are mildly autocorrelated and their
volatility clusters, so standard errors are Newey-West (heteroskedasticity- and
autocorrelation-consistent) with the usual ``4 (n / 100) ^ (2 / 9)`` lag rule.
"""

import math

import numpy as np
import pandas as pd
import statsmodels.api as sm

from app.models.factors import (
    MODEL_FACTORS,
    RISK_FREE,
    Coefficient,
    FactorExposure,
    FactorFit,
    FactorModel,
)
from app.stats.volatility import InsufficientDataError, clean_prices

TRADING_DAYS: int = 252
"""Days per year used to annualize daily alpha and volatility."""

MIN_OBSERVATIONS: int = 120
"""Fewest daily returns regressed at all, about six months."""

RECOMMENDED_OBSERVATIONS: int = 500
"""Returns needed for reasonably precise betas, about two years; fewer carry a warning."""

LOW_R_SQUARED: float = 0.1
"""R-squared below which the factors explain too little for the betas to mean much."""


def daily_returns(closes: pd.Series) -> pd.Series:
    """Return simple daily returns of ``closes``, indexed by calendar date without time zone.

    Prices are cleaned first, so a return spans from one valid close to the next.
    """
    prices = clean_prices(closes)
    return by_calendar_date(prices.pct_change().iloc[1:])


def by_calendar_date(returns: pd.Series) -> pd.Series:
    """Index daily ``returns`` by calendar date without time zone, as the factor files are."""
    returns = returns.copy()
    index = pd.DatetimeIndex(returns.index)
    if index.tz is not None:
        # Daily bars are stamped at midnight exchange time; dropping the zone keeps
        # the exchange's calendar date, which is how the factor files are dated.
        index = index.tz_localize(None)
    returns.index = index.normalize()
    return returns[~returns.index.duplicated(keep="last")]


def hac_lags(observations: int) -> int:
    """Return the Newey-West lag count for ``observations`` returns."""
    return math.floor(4 * math.pow(observations / 100, 2 / 9))


def _coefficient(estimate: float, std_error: float, p_value: float, scale: float) -> Coefficient:
    """Build a coefficient, scaled (for annualizing), with no t-stat when undefined."""
    if not std_error > 0:
        return Coefficient(estimate=estimate * scale, std_error=0.0, t_stat=None, p_value=None)
    return Coefficient(
        estimate=estimate * scale,
        std_error=std_error * scale,
        t_stat=estimate / std_error,
        p_value=float(p_value),
    )


def _warnings(observations: int, r_squared: float) -> list[str]:
    """Describe why a fit's estimates may be unreliable, if they are."""
    warnings: list[str] = []
    if observations < RECOMMENDED_OBSERVATIONS:
        warnings.append(
            f"The regression uses {observations} daily returns; at least "
            f"{RECOMMENDED_OBSERVATIONS} are recommended. Betas and especially alpha are "
            "imprecise; use a longer period."
        )
    if r_squared < LOW_R_SQUARED:
        warnings.append(
            f"The factors explain only {r_squared:.0%} of the return variance, so the "
            "stock's returns are mostly idiosyncratic and the betas say little about them."
        )
    return warnings


def fit_factor_model(closes: pd.Series, factors: pd.DataFrame, model: FactorModel) -> FactorFit:
    """Regress the daily excess returns of ``closes`` on the factors of ``model``.

    See :func:`fit_factor_returns`; returns are taken between consecutive valid closes.
    """
    return fit_factor_returns(daily_returns(closes), factors, model)


def fit_factor_returns(returns: pd.Series, factors: pd.DataFrame, model: FactorModel) -> FactorFit:
    """Regress daily excess ``returns`` on the factors of ``model``.

    Args:
        returns: Daily simple returns of a stock or strategy, indexed by bar timestamp.
        factors: Daily factor returns as decimals, indexed by date, with a column for
            every factor of ``model`` and for ``RF``, as returned by
            :func:`app.data.factors.fetch_factors`.
        model: ``"ff3"``, ``"carhart4"`` or ``"ff5"``.

    Returns:
        Annualized alpha, factor betas with Newey-West inference, fit statistics, and
        the split of return variance between the factors and the stock itself.

    Raises:
        InsufficientDataError: If fewer than :data:`MIN_OBSERVATIONS` days have both a
            return and factor returns, or the returns never change.
    """
    names = MODEL_FACTORS[model]
    data = pd.concat(
        [by_calendar_date(returns).rename("stock"), factors.loc[:, [*names, RISK_FREE]]],
        axis=1,
        join="inner",
    ).dropna()
    observations = len(data)
    if observations < MIN_OBSERVATIONS:
        raise InsufficientDataError(
            f"A factor regression needs at least {MIN_OBSERVATIONS} days with both stock "
            f"and factor returns; only {observations} are available. Request a longer "
            "period; factor data is published about a month late."
        )
    excess = data["stock"] - data[RISK_FREE]
    if float(excess.std(ddof=1)) == 0.0:
        raise InsufficientDataError(
            "Returns never change over the window; there is nothing to fit."
        )

    regressors = data.loc[:, names]
    lags = hac_lags(observations)
    result = sm.OLS(excess, sm.add_constant(regressors)).fit(
        cov_type="HAC", cov_kwds={"maxlags": lags, "use_correction": True}
    )
    params, errors, p_values = result.params, result.bse, result.pvalues

    variance = float(excess.var(ddof=1))
    covariances = regressors.apply(lambda column: column.cov(excess))
    r_squared = min(max(float(result.rsquared), 0.0), 1.0)
    annualize = math.sqrt(TRADING_DAYS)

    return FactorFit(
        start=data.index[0].date(),
        end=data.index[-1].date(),
        observations=observations,
        alpha=_coefficient(
            float(params["const"]), float(errors["const"]), p_values["const"], TRADING_DAYS
        ),
        exposures=[
            FactorExposure(
                factor=name,
                variance_share=float(params[name] * covariances[name] / variance),
                **_coefficient(
                    float(params[name]), float(errors[name]), p_values[name], 1.0
                ).model_dump(),
            )
            for name in names
        ],
        r_squared=r_squared,
        adjusted_r_squared=min(float(result.rsquared_adj), 1.0),
        total_volatility=math.sqrt(variance) * annualize,
        residual_volatility=float(np.sqrt(result.mse_resid)) * annualize,
        systematic_share=r_squared,
        idiosyncratic_share=1.0 - r_squared,
        hac_lags=lags,
        warnings=_warnings(observations, r_squared),
    )
