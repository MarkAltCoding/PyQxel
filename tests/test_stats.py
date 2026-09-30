"""Tests for the price summary behind AI analyses and the performance metrics behind backtests."""

import math

import numpy as np
import pandas as pd
import pytest

from app.stats.indicators import summarize_prices
from app.stats.metrics import MIN_METRIC_RETURNS, performance_metrics
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


ALTERNATING = [0.02, -0.01] * 10
"""Twenty returns: mean 0.005, sample deviation 0.015 * sqrt(20 / 19), downside RMS sqrt(5e-5)."""


def test_metrics_sharpe_and_sortino() -> None:
    """Sharpe divides by the sample deviation, Sortino by downside deviation over all bars."""
    metrics = performance_metrics(_series(ALTERNATING), periods_per_year=252)

    deviation = 0.015 * math.sqrt(20 / 19)
    assert metrics.observations == 20
    assert metrics.annualized_volatility == pytest.approx(deviation * math.sqrt(252))
    assert metrics.sharpe_ratio == pytest.approx(0.005 / deviation * math.sqrt(252))
    assert metrics.sortino_ratio == pytest.approx(0.005 / math.sqrt(5e-5) * math.sqrt(252))
    assert metrics.total_return == pytest.approx(1.02**10 * 0.99**10 - 1)


def test_metrics_subtract_the_risk_free_rate() -> None:
    """The annual risk-free rate is compounded down to a per-bar rate before subtracting."""
    rate = 1.05 ** (1 / 252) - 1
    metrics = performance_metrics(_series(ALTERNATING), 252, risk_free_rate=0.05)

    deviation = 0.015 * math.sqrt(20 / 19)
    assert metrics.sharpe_ratio == pytest.approx((0.005 - rate) / deviation * math.sqrt(252))
    downside = math.sqrt((10 * (0.01 + rate) ** 2 + 10 * min(0.02 - rate, 0) ** 2) / 20)
    assert metrics.sortino_ratio == pytest.approx((0.005 - rate) / downside * math.sqrt(252))


def test_metrics_annualize_only_full_years() -> None:
    """CAGR needs a year of bars and compounds the total return per year."""
    assert performance_metrics(_series([0.001] * 251), 252).annualized_return is None

    metrics = performance_metrics(_series([0.001] * 504), 252)
    assert metrics.annualized_return == pytest.approx(1.001**252 - 1)


def test_metrics_date_the_max_drawdown() -> None:
    """Peak, trough and recovery are the bars where equity peaks, bottoms and regains the peak."""
    # Equity: 1.1 on bar 0, 0.88 on bar 2 (a 20% drawdown), back to 1.1 on bar 4.
    returns = [0.1, -0.1, -1 / 9, 0.125, 1.1 / 0.99 - 1] + [0.0] * 15
    series = _series(returns)
    metrics = performance_metrics(series, 252, inception=pd.Timestamp("2023-12-29"))

    assert metrics.max_drawdown == pytest.approx(-0.2)
    assert metrics.max_drawdown_peak == series.index[0]
    assert metrics.max_drawdown_trough == series.index[2]
    assert metrics.max_drawdown_recovery == series.index[4]
    assert metrics.start == pd.Timestamp("2023-12-29")


def test_metrics_drawdown_from_inception_without_recovery() -> None:
    """A loss on the first bar is a drawdown from the capital invested at inception."""
    inception = pd.Timestamp("2023-12-29")
    metrics = performance_metrics(_series([-0.1] + [0.001] * 19), 252, inception=inception)

    assert metrics.max_drawdown == pytest.approx(-0.1)
    assert metrics.max_drawdown_peak == inception
    assert metrics.max_drawdown_recovery is None


def test_metrics_without_losses_or_variation() -> None:
    """Constant gains have no drawdown, no Sharpe and no Sortino."""
    metrics = performance_metrics(_series([0.001] * 30), 252)

    assert metrics.max_drawdown == 0.0
    assert metrics.max_drawdown_peak is None
    assert metrics.sharpe_ratio is None
    assert metrics.sortino_ratio is None


def test_metrics_drop_missing_returns_and_reject_short_series() -> None:
    """Missing and non-finite returns are dropped before the length check."""
    values = [0.01] * (MIN_METRIC_RETURNS - 1) + [np.nan, np.inf]
    with pytest.raises(InsufficientDataError, match="at least 20 returns"):
        performance_metrics(_series(values), 252)
