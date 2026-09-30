"""Descriptive return, risk and drawdown statistics for a close-price series.

These are the facts an analysis is written from, so every figure is computed here
rather than left to the language model. Returns and volatilities are decimals
(0.25 = 25%); volatilities are annualized.
"""

import math

import pandas as pd

from app.models.research import PriceSummary
from app.stats.ewma import DAILY_DECAY, MIN_OBSERVATIONS, ewma_variance
from app.stats.volatility import (
    annualization_scale,
    clean_prices,
    log_returns,
    require_returns,
)

MIN_SUMMARY_RETURNS: int = 20
"""Fewest returns a summary is computed from, about one trading month of daily bars."""

MIN_ANNUALIZING_DAYS: int = 365
"""Shortest calendar span whose return is annualized; shorter spans overstate it."""


def summarize_prices(
    closes: pd.Series, periods_per_year: int, decay: float = DAILY_DECAY
) -> PriceSummary:
    """Summarize the return, volatility and drawdown of ``closes``.

    Missing, non-finite and non-positive closes are dropped first, so gaps such as
    non-trading days never appear as flat returns.

    Args:
        closes: Close prices indexed by bar timestamp, in any order.
        periods_per_year: Bars per year, used to annualize (252 for daily bars).
        decay: EWMA decay for the current-volatility estimate.

    Returns:
        The summary. ``annualized_return`` is ``None`` for spans under a year and
        ``ewma_volatility`` is ``None`` when there are too few returns to seed EWMA.

    Raises:
        InsufficientDataError: If there are fewer than :data:`MIN_SUMMARY_RETURNS`
            returns or prices never change.
    """
    scale = annualization_scale(periods_per_year)
    returns = log_returns(closes)
    require_returns(returns, MIN_SUMMARY_RETURNS, "An analysis", "Request a longer period.")

    prices = clean_prices(closes)
    start, end = pd.Timestamp(prices.index[0]), pd.Timestamp(prices.index[-1])
    first_close, last_close = float(prices.iloc[0]), float(prices.iloc[-1])

    period_return = last_close / first_close - 1.0
    span_days = (end - start).days
    annualized_return = (
        (1.0 + period_return) ** (365.25 / span_days) - 1.0
        if span_days >= MIN_ANNUALIZING_DAYS
        else None
    )

    drawdowns = prices / prices.cummax() - 1.0
    ewma_volatility = (
        float(math.sqrt(ewma_variance(returns.to_numpy(dtype=float), decay)[-1]) * scale)
        if len(returns) >= MIN_OBSERVATIONS
        else None
    )

    return PriceSummary(
        start=start.to_pydatetime(),
        end=end.to_pydatetime(),
        observations=len(returns),
        first_close=first_close,
        last_close=last_close,
        high=float(prices.max()),
        low=float(prices.min()),
        period_return=period_return,
        annualized_return=annualized_return,
        realized_volatility=float(returns.std(ddof=1)) * scale,
        ewma_volatility=ewma_volatility,
        max_drawdown=float(drawdowns.min()),
        current_drawdown=float(drawdowns.iloc[-1]),
        best_return=math.expm1(float(returns.max()) / 100.0),
        worst_return=math.expm1(float(returns.min()) / 100.0),
    )
