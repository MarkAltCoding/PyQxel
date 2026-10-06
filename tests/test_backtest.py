"""Tests for the vectorized backtesting engine and its built-in strategies."""

import numpy as np
import pandas as pd
import pytest

from pydantic import ValidationError

from app.models.backtest import BuyAndHold, MeanReversion, SmaCrossover, TimeSeriesMomentum
from app.stats.backtest import (
    Strategy,
    buy_and_hold,
    cash_returns,
    mean_reversion,
    run_backtest,
    run_portfolio_backtest,
    sma_crossover,
    strategy_for,
    time_series_momentum,
)
from app.stats.volatility import InsufficientDataError


def _closes(values: list[float]) -> pd.Series:
    """Index ``values`` by consecutive business days."""
    return pd.Series(values, index=pd.bdate_range("2024-01-01", periods=len(values)))


def _long_on(bars: set[int]) -> Strategy:
    """Build a strategy that targets a full long position at the close of ``bars``."""

    def positions(closes: pd.Series) -> pd.Series:
        return pd.Series([1.0 if i in bars else 0.0 for i in range(len(closes))], closes.index)

    return positions


JUMP = [100.0] * 30 + [110.0] + [110.0] * 29
"""Flat prices with a single 10% gain on bar 30."""


def test_signal_is_held_over_the_next_bar() -> None:
    """A position set at a bar's close earns the next bar's return, never its own."""
    same_bar = run_backtest(_closes(JUMP), _long_on({30}), 252)
    bar_before = run_backtest(_closes(JUMP), _long_on({29}), 252)

    assert same_bar.metrics.total_return == 0.0
    assert bar_before.metrics.total_return == pytest.approx(0.1)
    assert bar_before.positions["asset"].iloc[29] == 1.0  # positions[i] is held over return i + 1


def test_buy_and_hold_matches_benchmark_without_costs() -> None:
    """Holding the asset reproduces its returns, and the equity curve starts at 1.0."""
    closes = _closes([100.0 + i + (i % 4) for i in range(60)])

    result = run_backtest(closes, buy_and_hold, 252)

    assert result.metrics == result.benchmark
    assert result.equity.iloc[0] == 1.0
    assert result.equity.index[0] == closes.index[0]
    assert result.equity.iloc[-1] == pytest.approx(closes.iloc[-1] / closes.iloc[0])
    assert result.trades == 1
    assert result.exposure == 1.0


def test_costs_are_charged_per_unit_of_turnover() -> None:
    """Entering costs one unit of turnover; reversing from long to short costs two."""
    closes = _closes([100.0] * 60)
    flips = {i for i in range(60) if i < 10}

    def long_then_short(prices: pd.Series) -> pd.Series:
        return pd.Series([1.0 if i in flips else -1.0 for i in range(len(prices))], prices.index)

    result = run_backtest(closes, long_then_short, 252, cost_bps=10.0)

    assert result.trades == 2
    assert result.returns.iloc[0] == pytest.approx(-0.001)
    assert result.returns.iloc[10] == pytest.approx(-0.002)
    assert result.metrics.total_return == pytest.approx(0.999 * 0.998 - 1)


def test_sma_crossover_waits_for_the_slow_window() -> None:
    """The strategy is flat until the slow average exists, then follows the crossover."""
    rising = [100.0 + i for i in range(40)]
    falling = [139.0 - i for i in range(40)]
    closes = _closes(rising + falling)

    positions = sma_crossover(fast=3, slow=10)(closes)
    shorted = sma_crossover(fast=3, slow=10, allow_short=True)(closes)

    assert (positions.iloc[:9] == 0.0).all()
    assert positions.iloc[20] == 1.0
    assert positions.iloc[-1] == 0.0
    assert shorted.iloc[-1] == -1.0
    assert (shorted.iloc[:9] == 0.0).all()


def test_sma_crossover_needs_bars_beyond_the_slow_window() -> None:
    """A window barely longer than the slow average is rejected."""
    with pytest.raises(InsufficientDataError, match="200-bar moving average"):
        run_backtest(_closes([100.0 + i for i in range(210)]), sma_crossover(50, 200), 252)


def test_strategy_for_builds_each_spec() -> None:
    """Specs map to their strategies."""
    closes = _closes([100.0 + i for i in range(40)])

    assert strategy_for(BuyAndHold()) is buy_and_hold
    crossover = strategy_for(SmaCrossover(fast=3, slow=10))(closes)
    pd.testing.assert_series_equal(crossover, sma_crossover(3, 10)(closes))


def test_invalid_closes_are_dropped_before_the_strategy_runs() -> None:
    """Missing and non-positive closes never reach the strategy or the returns."""
    values = [100.0 + i for i in range(40)]
    values[5], values[6] = np.nan, 0.0

    result = run_backtest(_closes(values), buy_and_hold, 252)

    assert len(result.returns) == 37
    assert np.isfinite(result.returns).all()


def test_non_finite_positions_are_rejected() -> None:
    """A strategy may not leave positions undefined."""

    def undefined(closes: pd.Series) -> pd.Series:
        return pd.Series(np.nan, index=closes.index)

    with pytest.raises(ValueError, match="non-finite position"):
        run_backtest(_closes([100.0 + i for i in range(40)]), undefined, 252)


def test_short_histories_are_rejected() -> None:
    """Too few closes for metrics is an insufficient-data error."""
    with pytest.raises(InsufficientDataError):
        run_backtest(_closes([100.0 + i for i in range(15)]), buy_and_hold, 252)


def test_time_series_momentum_follows_the_lookback_return() -> None:
    """Long while the price is above its level ``lookback`` bars ago, flat otherwise."""
    closes = _closes([100.0 + i for i in range(40)] + [140.0 - 2 * i for i in range(40)])

    positions = time_series_momentum(lookback=20)(closes)

    assert positions.iloc[:20].eq(0.0).all()  # No full lookback yet.
    assert positions.iloc[20:40].eq(1.0).all()
    assert positions.iloc[-1] == 0.0
    short = time_series_momentum(lookback=20, allow_short=True)(closes)
    assert short.iloc[-1] == -1.0


def test_time_series_momentum_skips_recent_bars() -> None:
    """With a skip, the signal ignores a reversal in the most recent bars."""
    closes = _closes([100.0 + i for i in range(60)] + [150.0 - 10 * i for i in range(5)])

    without_skip = time_series_momentum(lookback=40)(closes)
    with_skip = time_series_momentum(lookback=40, skip=10)(closes)

    assert without_skip.iloc[-1] == 0.0
    assert with_skip.iloc[-1] == 1.0


def test_mean_reversion_buys_stretched_falls_and_sells_at_the_mean() -> None:
    """A long opens at -entry_z and closes once the z-score is back to -exit_z."""
    path = [100.0, 101.0] * 15 + [90.0, 92.0, 96.0, 101.0, 100.0, 101.0]
    closes = _closes(path)

    positions = mean_reversion(window=10, entry_z=2.0, exit_z=0.0)(closes)

    plunge = 30
    assert positions.iloc[plunge] == 1.0
    exit_bar = next(i for i in range(plunge + 1, len(path)) if positions.iloc[i] == 0.0)
    assert exit_bar > plunge
    assert positions.iloc[plunge:exit_bar].eq(1.0).all()
    assert positions.iloc[:plunge].eq(0.0).all()


def test_mean_reversion_shorts_stretched_rises_when_allowed() -> None:
    path = [100.0, 101.0] * 15 + [112.0, 101.0]
    closes = _closes(path)

    long_only = mean_reversion(window=10, entry_z=2.0, exit_z=0.0)(closes)
    with_shorts = mean_reversion(window=10, entry_z=2.0, exit_z=0.0, allow_short=True)(closes)

    assert long_only.iloc[30] == 0.0
    assert with_shorts.iloc[30] == -1.0
    assert with_shorts.iloc[31] == 0.0


def test_new_strategy_specs_validate_their_windows() -> None:
    assert strategy_for(TimeSeriesMomentum(lookback=60, skip=5)) is not None
    assert strategy_for(MeanReversion(window=10, entry_z=1.5, exit_z=0.5)) is not None
    with pytest.raises(ValidationError):
        TimeSeriesMomentum(lookback=20, skip=20)
    with pytest.raises(ValidationError):
        MeanReversion(entry_z=1.0, exit_z=1.0)


def test_cash_returns_compound_daily_rates_over_each_bar() -> None:
    """A weekly bar earns five days of interest; days past the data repeat its last rate."""
    days = pd.bdate_range("2026-01-05", "2026-01-30")
    risk_free = pd.Series(0.001, index=days[:-5])
    weekly_bars = pd.DatetimeIndex(["2026-01-02", "2026-01-09", "2026-01-16", "2026-01-30"])

    cash = cash_returns(risk_free, weekly_bars.tz_localize("America/New_York"))

    assert cash.iloc[0] == pytest.approx(1.001**5 - 1)
    assert cash.iloc[1] == pytest.approx(1.001**5 - 1)
    assert cash.iloc[2] == pytest.approx(1.001**10 - 1)


def test_idle_and_short_capital_earn_cash_interest() -> None:
    """Flat capital earns the risk-free rate; a short earns it on its proceeds as well."""
    closes = _closes([100.0 * 1.01**i for i in range(30)])
    risk_free = pd.Series(0.0002, index=closes.index)

    def short(prices: pd.Series) -> pd.Series:
        return pd.Series(-1.0, index=prices.index)

    flat = run_backtest(closes, _long_on(set()), 252, risk_free=risk_free)
    shorted = run_backtest(closes, short, 252, risk_free=risk_free)

    assert flat.returns.to_numpy() == pytest.approx(0.0002)
    assert shorted.returns.to_numpy() == pytest.approx(-0.01 + 2 * 0.0002)


def test_portfolio_backtest_weights_each_holding() -> None:
    """Returns, costs and contributions are each holding's, scaled by its weight."""
    index = pd.bdate_range("2024-01-01", periods=40)
    prices = pd.DataFrame(
        {"UP": [100.0 * 1.01**i for i in range(40)], "DOWN": [100.0 * 0.99**i for i in range(40)]},
        index=index,
    )

    result = run_portfolio_backtest(
        prices, {"UP": 0.75, "DOWN": 0.25}, buy_and_hold, 252, cost_bps=100.0
    )

    assert result.benchmark_returns.to_numpy() == pytest.approx(0.75 * 0.01 + 0.25 * -0.01)
    # Both holdings are bought at the first close, paying 1% of each weight.
    assert result.returns.iloc[0] == pytest.approx(0.005 - 0.01)
    assert result.returns.iloc[1:].to_numpy() == pytest.approx(0.005)
    up, down = result.assets["UP"], result.assets["DOWN"]
    assert (up.weight, up.trades, down.trades) == (0.75, 1, 1)
    assert up.contribution == pytest.approx(0.75 * 0.01 * 39 - 0.0075)
    assert up.contribution + down.contribution == pytest.approx(result.returns.sum())
    assert result.exposure == 1.0
