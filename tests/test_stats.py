"""Tests for the price summary statistics behind AI analyses."""

import math

import numpy as np
import pandas as pd
import pytest

from app.stats.indicators import summarize_prices
from app.stats.volatility import InsufficientDataError


def _series(values: list[float], start: str = "2024-01-01") -> pd.Series:
    """Index ``values`` by consecutive business days."""
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)))


def test_summary_computes_return_and_drawdown() -> None:
    """Returns, extremes and drawdowns come from the closes."""
    values = [100.0, 110.0, 120.0, 90.0, 96.0] * 5
    summary = summarize_prices(_series(values), periods_per_year=252)

    assert summary.observations == len(values) - 1
    assert summary.first_close == 100.0
    assert summary.last_close == 96.0
    assert summary.high == 120.0
    assert summary.low == 90.0
    assert summary.period_return == pytest.approx(-0.04)
    assert summary.max_drawdown == pytest.approx(90.0 / 120.0 - 1.0)
    assert summary.current_drawdown == pytest.approx(96.0 / 120.0 - 1.0)
    assert summary.best_return == pytest.approx(0.1)
    assert summary.worst_return == pytest.approx(90.0 / 120.0 - 1.0)


def test_summary_skips_annualizing_short_windows() -> None:
    """Windows under a year have no annualized return; EWMA needs 30 returns."""
    summary = summarize_prices(_series([100.0 + i for i in range(25)]), periods_per_year=252)

    assert summary.annualized_return is None
    assert summary.ewma_volatility is None


def test_summary_annualizes_long_windows() -> None:
    """A two-year doubling annualizes to about 41%, and EWMA volatility is reported."""
    index = pd.bdate_range("2022-01-03", "2024-01-03")
    rng = np.random.default_rng(0)
    noise = rng.normal(0.0, 0.01, len(index))
    noise -= noise.mean()
    trend = np.linspace(0.0, math.log(2.0), len(index))
    closes = pd.Series(100.0 * np.exp(trend + noise - noise[0]), index=index)
    closes.iloc[-1] = 200.0

    summary = summarize_prices(closes, periods_per_year=252)

    span_years = (index[-1] - index[0]).days / 365.25
    assert summary.annualized_return == pytest.approx(2.0 ** (1 / span_years) - 1.0)
    assert summary.ewma_volatility is not None and summary.ewma_volatility > 0


def test_summary_ignores_missing_and_invalid_closes() -> None:
    """NaN and non-positive closes are dropped rather than treated as returns."""
    values = [100.0 + i for i in range(30)]
    values[5] = float("nan")
    values[10] = 0.0

    summary = summarize_prices(_series(values), periods_per_year=252)

    assert summary.observations == 27
    assert summary.low == 100.0


def test_summary_rejects_short_history() -> None:
    """Fewer than 20 returns cannot be summarized."""
    with pytest.raises(InsufficientDataError, match="at least 20 returns"):
        summarize_prices(_series([100.0 + i for i in range(10)]), periods_per_year=252)
