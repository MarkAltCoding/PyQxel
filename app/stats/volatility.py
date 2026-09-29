"""Return preparation and validation shared by the volatility models.

Every model works on percent log returns built by :func:`log_returns` and reports
volatility as an annualized decimal (0.25 = 25%) using :func:`annualization_scale`.
"""

import math

import numpy as np
import pandas as pd

MAX_HORIZON: int = 252
"""Most bars ahead a forecast may reach."""


class InsufficientDataError(ValueError):
    """Raised when a price series is too short or too flat to fit a model."""


def log_returns(closes: pd.Series) -> pd.Series:
    """Return percent log returns between consecutive valid closes.

    Missing, non-finite and non-positive closes are dropped first, so a return spans
    from one valid bar to the next. Non-trading days never appear as zero returns
    because the series only holds bars that traded.

    Args:
        closes: Close prices indexed by bar timestamp, in any order.

    Returns:
        Percent log returns indexed by the timestamp of the later bar, oldest first.
    """
    prices = pd.to_numeric(closes, errors="coerce").astype(float).sort_index()
    prices = prices[~prices.index.duplicated(keep="last")]
    prices = prices[np.isfinite(prices) & (prices > 0)]
    return (100.0 * np.log(prices).diff()).iloc[1:]


def require_returns(returns: pd.Series, minimum: int, model: str, advice: str) -> None:
    """Reject return series that are shorter than ``minimum`` or never vary.

    Args:
        returns: Percent log returns.
        minimum: Fewest returns ``model`` accepts.
        model: Model name used in the error message, e.g. ``"GARCH"``.
        advice: What to request instead when the series is too short.

    Raises:
        InsufficientDataError: If the series is too short or constant.
    """
    if len(returns) < minimum:
        raise InsufficientDataError(
            f"{model} needs at least {minimum} returns; only {len(returns)} are "
            f"available. {advice}"
        )
    if float(returns.std(ddof=1)) == 0.0:
        raise InsufficientDataError("Prices never change over the window; volatility is zero.")


def require_horizon(horizon: int) -> None:
    """Reject forecast horizons outside ``1..MAX_HORIZON``."""
    if not 1 <= horizon <= MAX_HORIZON:
        raise ValueError(f"horizon must be between 1 and {MAX_HORIZON}.")


def annualization_scale(periods_per_year: int) -> float:
    """Return the factor turning a per-bar percent volatility into an annualized decimal."""
    if periods_per_year < 1:
        raise ValueError("periods_per_year must be positive.")
    return math.sqrt(periods_per_year) / 100.0
