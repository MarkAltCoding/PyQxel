"""Aligned daily returns of several assets, for models of how they move together.

Closes are matched by calendar date, so listings on exchanges in different time zones
line up. Only dates on which every asset has a valid close are kept, and returns are
taken after that, so each row's returns span the same interval for every asset. A
date one market was closed is dropped for all, and the next return spans both days.
"""

from dataclasses import dataclass
from typing import cast

import numpy as np
import pandas as pd

from app.stats.volatility import InsufficientDataError

MIN_PANEL_OBSERVATIONS: int = 200
"""Fewest common daily returns a multi-asset model is fitted on, about ten months."""

LATE_START: pd.Timedelta = pd.Timedelta(days=7)
"""How much later than the others an asset's data must start to be named as the cause."""


@dataclass(frozen=True)
class ReturnPanel:
    """Simple daily returns of several assets on the dates they all traded."""

    returns: pd.DataFrame
    """Decimal returns, one column per asset, indexed by date, oldest first."""
    excluded_dates: int
    """Dates with a close for some assets but not all, left out of every column."""
    closes: pd.DataFrame
    """The closes the returns are taken from, on the same dates plus the one before."""

    @property
    def observations(self) -> int:
        """Number of common returns."""
        return len(self.returns)


def _by_date(closes: pd.DataFrame) -> pd.DataFrame:
    """Re-index closes by calendar date without time zone, keeping valid prices only."""
    frame = closes.apply(pd.to_numeric, errors="coerce").astype(float)
    index = pd.DatetimeIndex(frame.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    frame.index = index.normalize()
    frame = frame.groupby(level=0).last().sort_index()
    valid: pd.DataFrame = frame.where(np.isfinite(frame) & (frame > 0))
    return valid


def first_date(prices: pd.Series) -> pd.Timestamp:
    """Return the date of the first valid price in ``prices``, which must have one."""
    return pd.Timestamp(cast(pd.Timestamp, prices.first_valid_index()))


def _overlap_reason(prices: pd.DataFrame, excluded: int) -> str:
    """Explain why assets share few dates: a late listing, or mismatched market calendars."""
    starts = {str(symbol): first_date(prices[symbol]) for symbol in prices.columns}
    latest = max(starts, key=lambda symbol: starts[symbol])
    if starts[latest] - min(starts.values()) > LATE_START:
        return (
            f"{latest} has the latest data, from {starts[latest].date()}; request a longer "
            "period or leave it out."
        )
    return (
        f"{excluded} dates on which some of the markets were closed were left out; "
        "request a longer period."
    )


def return_panel(
    closes: pd.DataFrame, min_observations: int = MIN_PANEL_OBSERVATIONS
) -> ReturnPanel:
    """Align ``closes`` on the dates every asset has, and turn them into returns.

    Args:
        closes: Adjusted closes, one column per asset, indexed by bar timestamp. Missing,
            non-finite and non-positive values count as no close that day.
        min_observations: Fewest common returns accepted.

    Returns:
        The aligned returns and how many dates were left out for lack of a close.

    Raises:
        InsufficientDataError: If an asset has too few closes, naming it and when its
            data starts; if the assets share too few dates; or if an asset's price never
            changes.
    """
    prices = _by_date(closes)
    counts = prices.notna().sum()
    short = [symbol for symbol in prices.columns if counts[symbol] < min_observations + 1]
    if short:
        details = "; ".join(
            f"{symbol} has {counts[symbol]} daily closes"
            + (f" (from {first_date(prices[symbol]).date()})" if counts[symbol] else "")
            for symbol in short
        )
        raise InsufficientDataError(
            f"Each asset needs at least {min_observations + 1} daily closes in the window; "
            f"{details}. Request a longer period or leave out recent listings."
        )

    common = prices.dropna()
    returns = common.pct_change().iloc[1:]
    excluded = int(len(prices) - len(common))
    if len(returns) < min_observations:
        raise InsufficientDataError(
            f"The assets share only {len(returns)} daily returns; at least "
            f"{min_observations} are needed. {_overlap_reason(prices, excluded)}"
        )
    flat = [symbol for symbol in returns.columns if float(returns[symbol].std(ddof=1)) == 0.0]
    if flat:
        raise InsufficientDataError(
            f"Prices never change over the window for {', '.join(flat)}; leave them out."
        )
    return ReturnPanel(returns=returns, excluded_dates=excluded, closes=common)
