"""How a holding's price moved over a window, and where it sits in its 52-week range.

Both read daily bars adjusted for splits and dividends, so a change is a total return:
what the holding earned with its dividends reinvested, not its price change alone.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

LISTING_TOLERANCE: timedelta = timedelta(days=7)
"""How long after a window's start the first bar may come and still count as covering it,
absorbing weekends and market holidays at the start of a holding's data."""

RANGE_DAYS: int = 365
"""Calendar days a 52-week range spans, back from the latest bar."""


@dataclass(frozen=True)
class WindowChange:
    """A holding's total return from the close on ``start`` to its latest close."""

    start: date
    change: float


@dataclass(frozen=True)
class PriceRange:
    """The lowest and highest prices over the last 52 weeks, and the latest close."""

    low: float
    high: float
    latest: float


def _dates(index: pd.Index) -> list[date]:
    """Each bar's calendar date in its exchange's time zone."""
    return [stamp.date() for stamp in pd.DatetimeIndex(index)]


def change_since(closes: pd.Series, start: date) -> WindowChange | None:
    """The total return from the last close on or before ``start`` to the latest close.

    Args:
        closes: Adjusted daily closes, oldest first, indexed by bar time.
        start: The first day of the window.

    Returns:
        The change, measured from the close it starts at, or ``None`` when there are no
        closes, or they begin more than :data:`LISTING_TOLERANCE` after ``start``, as for a
        holding listed during the window, whose change over the whole window is unknown.
    """
    closes = closes.dropna()
    closes = closes[closes > 0]
    if closes.empty:
        return None
    dates = _dates(closes.index)
    before = [i for i, day in enumerate(dates) if day <= start]
    if before:
        base = before[-1]
    elif dates[0] - start <= LISTING_TOLERANCE:
        base = 0
    else:
        return None
    return WindowChange(start=dates[base], change=float(closes.iloc[-1] / closes.iloc[base] - 1.0))


def price_range(bars: pd.DataFrame) -> PriceRange | None:
    """The 52-week low and high of daily ``bars``, from their lows and highs where given
    and their closes otherwise, with the latest close; ``None`` without closes."""
    bars = bars[bars["Close"].notna() & (bars["Close"] > 0)]
    if bars.empty:
        return None
    end = _dates(bars.index)[-1]
    recent = bars[[day > end - timedelta(days=RANGE_DAYS) for day in _dates(bars.index)]]
    lows = recent["Low"].where(recent["Low"] > 0).fillna(recent["Close"])
    highs = recent["High"].where(recent["High"] > 0).fillna(recent["Close"])
    latest = float(recent["Close"].iloc[-1])
    return PriceRange(
        low=min(float(lows.min()), latest), high=max(float(highs.max()), latest), latest=latest
    )
