"""Tests for one stock's screening metrics."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.stats.screening import (
    fundamental_metrics,
    liquidity,
    market_cap_ranks,
    price_metrics,
    screen_metrics,
)
from tests.financials import sample_financials

TODAY = date(2026, 10, 1)


def _prices(closes: list[float] | np.ndarray, volume: float = 1_000.0) -> pd.DataFrame:
    """Daily closes and a constant volume on business days ending before ``TODAY``."""
    index = pd.bdate_range(end="2026-09-30", periods=len(closes))
    return pd.DataFrame({"Close": closes, "Volume": volume}, index=index)


def test_liquidity_averages_the_last_three_months() -> None:
    """Dollar volume averages close times volume over the last 63 days only."""
    closes = [1.0] * 100 + [10.0] * 63

    trading = liquidity(_prices(closes, volume=1_000.0))

    assert trading is not None
    assert (trading.price, trading.avg_dollar_volume) == (10.0, 10_000.0)


def test_momentum_returns_skip_the_latest_month() -> None:
    """12-1 momentum runs from 252 to 21 trading days back; shorter returns end today."""
    closes = pd.Series(np.arange(1.0, 301.0))

    metrics = price_metrics(closes)

    assert metrics["return_1m"] == pytest.approx(300 / 279 - 1)
    assert metrics["return_6m"] == pytest.approx(300 / 174 - 1)
    assert metrics["momentum_12_1"] == pytest.approx(279 / 48 - 1)
    assert metrics["max_drawdown"] == 0.0


def test_short_histories_have_no_long_returns() -> None:
    """A recent listing has no 6-month or 12-1 momentum."""
    metrics = price_metrics(pd.Series(np.linspace(10.0, 12.0, 60)))

    assert metrics["return_1m"] is not None
    assert (metrics["return_6m"], metrics["momentum_12_1"]) == (None, None)


def test_volatility_and_drawdown_cover_the_last_year() -> None:
    """A fall before the last year does not count; one within it does."""
    closes = pd.Series([100.0] * 10 + [50.0] + [100.0] * 260 + [80.0, 100.0])

    metrics = price_metrics(closes)

    assert metrics["max_drawdown"] == pytest.approx(-0.2)
    assert metrics["volatility"] is not None and metrics["volatility"] > 0


def _factors(dates: pd.DatetimeIndex, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    table = pd.DataFrame(
        rng.normal(0, 0.01, (len(dates), 4)), index=dates, columns=["Mkt-RF", "SMB", "HML", "Mom"]
    )
    table["RF"] = 0.0
    return table


def test_screen_metrics_combine_prices_fundamentals_and_betas() -> None:
    """A stock built to have a market beta of 1.5 gets it back, beside its valuation."""
    dates = pd.bdate_range(end="2026-09-30", periods=300)
    factors = _factors(dates)
    returns = 1.5 * factors["Mkt-RF"] + np.random.default_rng(1).normal(0, 0.002, len(dates))
    closes = 100.0 * (1.0 + returns).cumprod()
    prices = pd.DataFrame({"Close": closes, "Volume": 1e6}, index=dates)

    metrics = screen_metrics(prices, 1_000.0, sample_financials(), factors, TODAY)

    assert metrics is not None
    assert metrics.beta_market == pytest.approx(1.5, abs=0.05)
    assert metrics.factor_r_squared is not None and metrics.factor_r_squared > 0.9
    assert metrics.pe_ratio == 10.0
    assert metrics.revenue_growth == 0.1


def test_betas_are_null_without_factor_data() -> None:
    """Without factors the other metrics are still computed."""
    metrics = screen_metrics(_prices(np.linspace(10, 20, 300)), 1e9, None, None, TODAY)

    assert metrics is not None
    assert metrics.beta_market is None and metrics.pe_ratio is None
    assert metrics.return_1m is not None


def test_stale_financials_give_no_ratios() -> None:
    """A company years behind on filing is not valued on old figures."""
    stale = sample_financials(latest_period_end=TODAY - timedelta(days=800))

    metrics = fundamental_metrics(stale, 10.0, 1_000.0, TODAY)

    assert all(value is None for value in metrics.values())


def test_market_cap_ranks_put_the_largest_first() -> None:
    assert market_cap_ranks({"S": 1.0, "L": 100.0, "M": 10.0}) == {"L": 1, "M": 2, "S": 3}
