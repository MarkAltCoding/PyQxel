"""Tests for the vectorized backtesting engine and its built-in strategies."""

import numpy as np
import pandas as pd
import pytest

from app.models.backtest import BuyAndHold, SmaCrossover
from app.stats.backtest import (
    Strategy,
    buy_and_hold,
    run_backtest,
    sma_crossover,
    strategy_for,
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
    assert bar_before.positions.iloc[29] == 1.0  # positions[i] is held over return i + 1


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
