"""Tests for a holding's change over a window and its 52-week range."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.stats.performance import change_since, price_range


def _closes(start: str, end: str, values: list[float] | None = None) -> pd.Series:
    """Daily closes on business days, in New York time, rising by one a day unless given."""
    index = pd.bdate_range(start, end, tz="America/New_York")
    data = values if values is not None else list(np.arange(len(index)) + 100.0)
    return pd.Series(data, index=index, dtype=float)


def test_change_starts_from_the_last_close_on_or_before_the_first_day() -> None:
    closes = _closes("2025-01-01", "2025-03-31")
    # Saturday, March 1: the window starts from Friday's close.
    found = change_since(closes, date(2025, 3, 1))
    assert found is not None
    assert found.start == date(2025, 2, 28)
    friday = closes[pd.DatetimeIndex(closes.index).date == date(2025, 2, 28)].iloc[0]
    assert found.change == pytest.approx(closes.iloc[-1] / friday - 1)


def test_a_window_starting_just_before_the_first_close_still_counts() -> None:
    closes = _closes("2025-01-06", "2025-03-31")
    found = change_since(closes, date(2025, 1, 1))
    assert found is not None and found.start == date(2025, 1, 6)


def test_a_holding_listed_during_the_window_has_no_change() -> None:
    closes = _closes("2025-02-03", "2025-03-31")
    assert change_since(closes, date(2025, 1, 1)) is None
    assert change_since(pd.Series(dtype=float), date(2025, 1, 1)) is None


def test_missing_and_zero_closes_are_skipped() -> None:
    closes = _closes("2025-01-01", "2025-01-10", [np.nan, 0.0, 100, 101, 102, 103, 104, 110])
    found = change_since(closes, date(2025, 1, 2))
    assert found is not None and found.change == pytest.approx(0.1)


def test_the_range_covers_52_weeks_of_lows_and_highs_and_the_latest_close() -> None:
    closes = _closes("2024-01-01", "2025-06-30")
    bars = pd.DataFrame({"Close": closes, "High": closes + 5, "Low": closes - 5})
    # A spike more than a year before the last bar is outside the range.
    bars.loc[bars.index[10], "High"] = 10_000.0
    bars.loc[bars.index[-30], "Low"] = np.nan
    found = price_range(bars)
    assert found is not None
    recent = bars[pd.DatetimeIndex(bars.index).date > date(2024, 6, 30)]
    assert found.high == pytest.approx(recent["High"].max())
    assert found.low == pytest.approx(recent["Low"].min())
    assert found.latest == pytest.approx(closes.iloc[-1])


def test_the_range_stretches_to_include_the_latest_close() -> None:
    closes = _closes("2025-01-01", "2025-02-28")
    bars = pd.DataFrame({"Close": closes, "High": closes * 0.5, "Low": closes * 0.4})
    found = price_range(bars)
    assert found is not None and found.high == found.latest
    assert price_range(bars.iloc[0:0]) is None
