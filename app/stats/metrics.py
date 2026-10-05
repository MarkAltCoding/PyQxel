"""Performance metrics of a periodic return series: returns, Sharpe, Sortino and drawdown.

Inputs are simple (not log) returns as decimals, one per bar, so a strategy's
returns compound as ``(1 + r).prod()``. Definitions follow common practice and match
QuantStats: Sharpe and Sortino use the arithmetic mean excess return, annualized by
``sqrt(periods_per_year)``; downside deviation is the root mean square of excess
returns below zero, taken over every bar.
"""

import math

import numpy as np
import pandas as pd

from app.models.backtest import PerformanceMetrics
from app.stats.volatility import InsufficientDataError

MIN_METRIC_RETURNS: int = 20
"""Fewest returns metrics are computed from, about one trading month of daily bars."""

FLAT_TOLERANCE: float = 1e-12
"""Dispersion below which returns count as constant, absorbing floating-point noise."""


def clean_returns(returns: pd.Series) -> pd.Series:
    """Return ``returns`` as floats sorted by time, without duplicates or non-finite values."""
    cleaned = pd.to_numeric(returns, errors="coerce").astype(float).sort_index()
    cleaned = cleaned[~cleaned.index.duplicated(keep="last")]
    return cleaned[np.isfinite(cleaned)]


def per_period_rate(annual_rate: float, periods_per_year: int) -> float:
    """Convert an annual rate to the compounding-equivalent rate per bar."""
    return math.pow(1.0 + annual_rate, 1.0 / periods_per_year) - 1.0


def _timestamp(value: object) -> pd.Timestamp:
    """Return an index label as a timestamp."""
    return pd.Timestamp(value)  # type: ignore[arg-type]


def performance_metrics(
    returns: pd.Series,
    periods_per_year: int,
    risk_free_rate: float = 0.0,
    inception: pd.Timestamp | None = None,
) -> PerformanceMetrics:
    """Compute return, risk-adjusted return and drawdown metrics of ``returns``.

    Args:
        returns: Simple returns per bar as decimals, indexed by the bar they end on.
            Missing and non-finite values are dropped.
        periods_per_year: Bars per year, used to annualize (252 for daily bars).
        risk_free_rate: Annual risk-free rate as a decimal, subtracted before the
            Sharpe and Sortino ratios are taken.
        inception: When capital was invested, normally the close of the bar before the
            first return. It dates the window start and a drawdown that begins at once.

    Returns:
        The metrics. ``annualized_return`` is ``None`` for fewer than a year of bars.

    Raises:
        InsufficientDataError: If fewer than :data:`MIN_METRIC_RETURNS` returns remain.
    """
    series = clean_returns(returns)
    if len(series) < MIN_METRIC_RETURNS:
        raise InsufficientDataError(
            f"Performance metrics need at least {MIN_METRIC_RETURNS} returns; only "
            f"{len(series)} are available. Request a longer period."
        )
    values = series.to_numpy(dtype=float)
    observations = len(values)
    scale = math.sqrt(periods_per_year)

    equity = np.concatenate(([1.0], np.cumprod(1.0 + values)))
    total_return = float(equity[-1] - 1.0)
    annualized_return = (
        float(max(1.0 + total_return, 0.0) ** (periods_per_year / observations) - 1.0)
        if observations >= periods_per_year
        else None
    )

    excess = values - per_period_rate(risk_free_rate, periods_per_year)
    volatility = float(np.std(values, ddof=1))
    sharpe = float(excess.mean() / volatility * scale) if volatility > FLAT_TOLERANCE else None
    downside = float(np.sqrt(np.mean(np.minimum(excess, 0.0) ** 2)))
    sortino = float(excess.mean() / downside * scale) if downside > FLAT_TOLERANCE else None

    # Position 0 of the equity curve is the capital at inception, before any return.
    times: list[pd.Timestamp | None] = [inception, *(_timestamp(t) for t in series.index)]
    peaks = np.maximum.accumulate(equity)
    drawdowns = equity / peaks - 1.0
    trough = int(np.argmin(drawdowns))
    max_drawdown = float(drawdowns[trough])
    peak_time = trough_time = recovery_time = None
    if max_drawdown < 0.0:
        peak = int(np.flatnonzero(equity[: trough + 1] == peaks[trough])[-1])
        recovered = np.flatnonzero(equity[trough:] >= peaks[trough])
        peak_time, trough_time = times[peak], times[trough]
        recovery_time = times[trough + int(recovered[0])] if recovered.size else None

    first = inception if inception is not None else _timestamp(series.index[0])
    return PerformanceMetrics(
        start=first.to_pydatetime(),
        end=_timestamp(series.index[-1]).to_pydatetime(),
        observations=observations,
        total_return=total_return,
        annualized_return=annualized_return,
        annualized_volatility=volatility * scale,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown=max_drawdown,
        max_drawdown_peak=None if peak_time is None else peak_time.to_pydatetime(),
        max_drawdown_trough=None if trough_time is None else trough_time.to_pydatetime(),
        max_drawdown_recovery=None if recovery_time is None else recovery_time.to_pydatetime(),
    )
