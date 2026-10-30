"""Aligned daily returns of several assets, for models of how they move together.

Closes are matched by calendar date, so listings on exchanges in different time zones
line up. Only dates on which every asset has a valid close are kept, and returns are
taken after that, so each row's returns span the same interval for every asset. A
date one market was closed is dropped for all, and the next return spans both days.
"""

from dataclasses import dataclass
from typing import Literal, cast

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


DropReason = Literal["cash", "short_history", "late_start"]
"""Why :func:`usable_return_panel` left an asset out: its price never moves, so it acts
as cash; it has too few closes; or its data starts so late that keeping it would leave
the others too few common days."""


@dataclass(frozen=True)
class Dropped:
    """An asset left out of a return panel, and why."""

    symbol: str
    reason: DropReason
    detail: str
    """The reason in words, e.g. ``170 daily closes, from 2026-02-27``."""


def _history(prices: pd.Series, count: int) -> str:
    """Describe how much of the window ``prices`` covers."""
    if not count:
        return "no daily closes in the window"
    return f"{count} daily closes, from {first_date(prices).date()}"


def usable_return_panel(
    closes: pd.DataFrame,
    min_observations: int = MIN_PANEL_OBSERVATIONS,
    max_history_cut: float | None = None,
) -> tuple[ReturnPanel, list[Dropped]]:
    """Like :func:`return_panel`, but leave out the assets that keep the rest from a fit.

    Assets with fewer than ``min_observations + 1`` closes, and those whose price never
    changes, are left out first. With ``max_history_cut``, so is any asset whose data
    starts so late that keeping it would cut more than that fraction off the history
    the earliest-starting asset has: every asset must have a return on each day, so the
    latest start sets where the fit begins. If the rest still share too few dates, the
    asset whose data starts latest is left out, and so on, as long as a late start is
    the cause.

    Returns:
        The panel of the assets kept, and those left out with why, in column order.

    Raises:
        InsufficientDataError: If no asset can be kept, or the ones that can share too
            few dates because their markets keep different calendars.
    """
    prices = _by_date(closes)
    counts = prices.notna().sum()
    dropped: list[Dropped] = []
    for symbol in prices.columns:
        count = int(counts[symbol])
        if count < min_observations + 1:
            detail = _history(prices[symbol], count)
            dropped.append(Dropped(str(symbol), "short_history", detail))
        elif float(prices[symbol].dropna().pct_change().iloc[1:].abs().max()) == 0.0:
            dropped.append(Dropped(str(symbol), "cash", "its price never changes"))
    kept = [str(symbol) for symbol in prices.columns if symbol not in {d.symbol for d in dropped}]

    if max_history_cut is not None and len(kept) > 1:
        starts = {symbol: first_date(prices[symbol]) for symbol in kept}
        earliest = min(starts.values())
        span = pd.Timestamp(cast(pd.Timestamp, prices[kept].last_valid_index())) - earliest
        for symbol in list(kept):
            cut = (starts[symbol] - earliest) / span if span > pd.Timedelta(0) else 0.0
            if cut > max_history_cut:
                kept.remove(symbol)
                dropped.append(
                    Dropped(
                        symbol,
                        "late_start",
                        f"its data starts {starts[symbol].date()}, which would cut "
                        f"{cut:.0%} off the others' history, more than the "
                        f"{max_history_cut:.0%} allowed",
                    )
                )

    while kept:
        shared = len(prices[kept].dropna()) - 1
        if shared >= min_observations or len(kept) == 1:
            break
        starts = {symbol: first_date(prices[symbol]) for symbol in kept}
        latest = max(kept, key=lambda symbol: starts[symbol])
        if starts[latest] - min(starts.values()) <= LATE_START:
            break
        kept.remove(latest)
        dropped.append(
            Dropped(
                latest,
                "late_start",
                f"its data starts {starts[latest].date()}, leaving the others only "
                f"{max(shared, 0)} common days",
            )
        )
    if not kept:
        details = "; ".join(f"{d.symbol}: {d.detail}" for d in dropped)
        raise InsufficientDataError(
            f"None of the assets can be modeled, each needing {min_observations + 1} daily "
            f"closes that move; {details}. Request a longer period."
        )
    order = {str(symbol): i for i, symbol in enumerate(prices.columns)}
    dropped.sort(key=lambda d: order[d.symbol])
    return return_panel(closes[kept], min_observations), dropped


def pair_returns(closes: pd.DataFrame, first: str, second: str) -> pd.DataFrame:
    """Simple daily returns of two of ``closes``' assets on every date both traded, so a
    pair is measured over all the history it shares rather than the whole panel's."""
    both = _by_date(closes[[first, second]]).dropna()
    returns: pd.DataFrame = both.pct_change().iloc[1:]
    return returns


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
