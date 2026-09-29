"""Exponentially weighted moving average (EWMA) volatility, the RiskMetrics model.

Variance is updated recursively with a fixed decay ``λ``::

    σ²[t] = λ · σ²[t-1] + (1 - λ) · r²[t-1]

Nothing is estimated, so the model works on histories far too short for GARCH. Returns
are assumed to have zero mean, as in RiskMetrics. The recursion is seeded with the
mean squared return over the first :data:`SEED_WINDOW` returns. EWMA has no long-run
level to revert to, so every forecast step equals the one-step-ahead volatility.
"""

import math

import numpy as np
import pandas as pd

from app.models.volatility import EwmaFit, VolatilityForecastStep, VolatilityPoint
from app.stats.volatility import (
    annualization_scale,
    log_returns,
    require_horizon,
    require_returns,
)

DAILY_DECAY: float = 0.94
"""RiskMetrics decay for daily returns; a return's weight halves in about 11 bars."""

WEEKLY_DECAY: float = 0.97
"""Decay for weekly returns; a return's weight halves in about 23 bars."""

MIN_OBSERVATIONS: int = 30
"""Fewest returns accepted; enough to seed the recursion."""

SEED_WINDOW: int = 30
"""Returns averaged to seed the variance recursion."""

MAX_SEED_WEIGHT: float = 0.05
"""Weight the seed may keep in the current estimate before a fit carries a warning."""


def ewma_variance(returns: np.ndarray, decay: float) -> np.ndarray:
    """Run the EWMA variance recursion over ``returns``.

    Args:
        returns: Returns, oldest first.
        decay: Weight ``λ`` kept by the previous variance, in ``(0, 1)``.

    Returns:
        ``len(returns) + 1`` variances. Element ``t`` is the variance of return ``t``
        given returns before it; the last element is the one-step-ahead forecast.
    """
    variance = np.empty(len(returns) + 1)
    variance[0] = float(np.mean(returns[:SEED_WINDOW] ** 2))
    for t, value in enumerate(returns, start=1):
        variance[t] = decay * variance[t - 1] + (1.0 - decay) * value * value
    return variance


def _warnings(observations: int, decay: float) -> list[str]:
    """Describe why an EWMA estimate may be unreliable, if it is."""
    seed_weight = decay**observations
    if seed_weight <= MAX_SEED_WEIGHT:
        return []
    return [
        f"Only {observations} returns: the seed variance still carries "
        f"{seed_weight:.0%} of the weight in the current estimate. Use a longer period."
    ]


def fit_ewma(
    closes: pd.Series,
    periods_per_year: int,
    horizon: int = 10,
    decay: float = DAILY_DECAY,
) -> EwmaFit:
    """Estimate EWMA volatility from the log returns of ``closes`` and forecast it.

    Args:
        closes: Close prices indexed by bar timestamp.
        periods_per_year: Bars per year, used to annualize (252 for daily bars).
        horizon: Bars ahead to forecast, from 1 to
            :data:`~app.stats.volatility.MAX_HORIZON`.
        decay: Weight ``λ`` kept by the previous variance, in ``(0, 1)``. The default
            :data:`DAILY_DECAY` suits daily bars; use :data:`WEEKLY_DECAY` for weekly.

    Returns:
        In-sample and forecast volatility, with warnings when the sample is short.

    Raises:
        ValueError: If ``periods_per_year``, ``horizon`` or ``decay`` is out of range.
        InsufficientDataError: If there are too few returns or prices never change.
    """
    scale = annualization_scale(periods_per_year)
    require_horizon(horizon)
    if not 0.0 < decay < 1.0:
        raise ValueError("decay must be between 0 and 1, exclusive.")

    returns = log_returns(closes)
    require_returns(
        returns, MIN_OBSERVATIONS, "EWMA", "Request a longer period or a shorter interval."
    )

    sigma = np.sqrt(ewma_variance(returns.to_numpy(dtype=float), decay)) * scale
    return EwmaFit(
        decay=decay,
        observations=len(returns),
        half_life=math.log(0.5) / math.log(decay),
        current_volatility=float(sigma[-2]),
        realized_volatility=float(returns.std(ddof=1)) * scale,
        conditional_volatility=[
            VolatilityPoint(timestamp=pd.Timestamp(timestamp).to_pydatetime(), volatility=value)
            for timestamp, value in zip(returns.index, sigma[:-1].tolist())
        ],
        forecast=[
            VolatilityForecastStep(step=step, volatility=float(sigma[-1]))
            for step in range(1, horizon + 1)
        ],
        warnings=_warnings(len(returns), decay),
    )
