"""Tests for the vectorized backtesting engine and its built-in strategies."""

import numpy as np
import pandas as pd
import pytest

from pydantic import ValidationError

from app.models.backtest import (
    BollingerBands,
    BuyAndHold,
    DonchianBreakout,
    DualMomentum,
    Macd,
    MeanReversion,
    Rsi,
    SmaCrossover,
    StrategySpec,
    TimeSeriesMomentum,
    VolatilityTargeting,
)
from app.stats.backtest import (
    MisalignedPositionsError,
    Strategy,
    bollinger_breakout,
    buy_and_hold,
    cash_returns,
    donchian_breakout,
    dual_momentum,
    macd,
    mean_reversion,
    replay_positions,
    rsi,
    rsi_reversal,
    run_backtest,
    run_portfolio_backtest,
    sma_crossover,
    strategy_for,
    time_series_momentum,
    volatility_targeting,
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


def _random_walk(bars: int, seed: int = 0, volatility: float = 0.015) -> pd.Series:
    """A geometric random walk of daily closes."""
    steps = np.random.default_rng(seed).normal(0.0003, volatility, bars)
    return _closes(list(100.0 * np.exp(np.cumsum(steps))))


def test_rsi_is_wilder_smoothed_and_bounded() -> None:
    """RSI is 100 after only gains, 0 after only losses, and 50 when nothing moved."""
    assert rsi(_closes([100.0 + i for i in range(20)]), 14).iloc[-1] == 100.0
    assert rsi(_closes([100.0 - i for i in range(20)]), 14).iloc[-1] == 0.0
    flat = rsi(_closes([100.0] * 20), 14)
    assert flat.iloc[:14].isna().all()
    assert flat.iloc[-1] == 50.0
    values = rsi(_random_walk(300), 14).dropna()
    assert ((values >= 0) & (values <= 100)).all()


def test_rsi_reversal_buys_oversold_and_sells_overbought() -> None:
    """A slide opens a long that is held through the bounce until RSI is overbought."""
    path = (
        [100.0, 101.0] * 10
        + [100.0 - 2 * i for i in range(1, 11)]
        + [80.0 + 3 * i for i in range(1, 16)]
    )
    closes = _closes(path)
    strength = rsi(closes, 14)

    positions = rsi_reversal(14, 30.0, 70.0)(closes)

    opened = next(i for i in range(len(path)) if positions.iloc[i] == 1.0)
    closed = next(i for i in range(opened, len(path)) if positions.iloc[i] == 0.0)
    assert strength.iloc[opened] <= 30.0 < strength.iloc[opened - 1]
    assert strength.iloc[closed] >= 70.0
    assert (strength.iloc[opened:closed] < 70.0).all()
    shorted = rsi_reversal(14, 30.0, 70.0, allow_short=True)(closes)
    assert shorted.iloc[closed] == -1.0


def test_bollinger_reversion_reuses_mean_reversion_at_the_middle_band() -> None:
    closes = _random_walk(300)

    bands = strategy_for(BollingerBands(window=20, num_std=1.5))(closes)

    pd.testing.assert_series_equal(bands, mean_reversion(20, 1.5, 0.0)(closes))


def test_bollinger_breakout_follows_the_upper_band_until_the_average() -> None:
    """A surge through the upper band opens a long that closes back at the average."""
    path = [100.0, 101.0] * 15 + [110.0, 112.0, 113.0, 100.0]
    closes = _closes(path)

    positions = bollinger_breakout(10, 2.0)(closes)

    assert positions.iloc[:30].eq(0.0).all()
    assert positions.iloc[30:33].eq(1.0).all()
    assert positions.iloc[33] == 0.0
    shorted = bollinger_breakout(10, 2.0, allow_short=True)(_closes([100.0, 101.0] * 15 + [90.0]))
    assert shorted.iloc[-1] == -1.0


def test_donchian_breakout_buys_new_highs_and_sells_new_lows() -> None:
    """Long from a close above the prior entry-bar high until one below the exit-bar low."""
    path = [100.0, 101.0] * 20 + [105.0, 106.0, 107.0, 104.0, 99.0, 98.0]
    closes = _closes(path)

    positions = donchian_breakout(entry=20, exit=3)(closes)

    assert positions.iloc[:40].eq(0.0).all()
    assert positions.iloc[40:43].eq(1.0).all()
    assert positions.iloc[43] == 0.0  # 104 is below the 105 to 107 of the last three bars.
    shorted = donchian_breakout(entry=20, exit=3, allow_short=True)(closes)
    assert shorted.iloc[44] == -1.0  # 99 is below every close of the previous 20 bars.


def test_dual_momentum_needs_a_positive_return_that_beats_the_benchmark() -> None:
    """Flat while the asset falls, or rises less than the benchmark; long otherwise."""
    rising = _closes([100.0 + i for i in range(60)])
    faster = _closes([100.0 + 2 * i for i in range(60)])
    falling = _closes([100.0 - 0.5 * i for i in range(60)])

    assert dual_momentum(20, 0, falling, "BENCH")(rising).iloc[20:].eq(1.0).all()
    assert dual_momentum(20, 0, faster, "BENCH")(rising).eq(0.0).all()
    assert dual_momentum(20, 0, falling, "BENCH")(falling).eq(0.0).all()
    # Against itself, only the absolute test remains.
    assert dual_momentum(20, 0, rising, "BENCH")(rising).iloc[20:].eq(1.0).all()


def test_dual_momentum_matches_benchmark_dates_across_time_zones() -> None:
    """A benchmark in another time zone, missing a holiday, is matched by calendar date."""
    asset = pd.Series(
        [100.0 + i for i in range(60)],
        index=pd.bdate_range("2024-01-01", periods=60, tz="Europe/London"),
    )
    benchmark = pd.Series(
        [100.0 - 0.5 * i for i in range(60)],
        index=pd.bdate_range("2024-01-01", periods=60, tz="America/New_York"),
    ).drop(pd.Timestamp("2024-02-05", tz="America/New_York"))

    positions = dual_momentum(20, 0, benchmark, "SPY")(asset)

    assert positions.iloc[20:].eq(1.0).all()


def test_dual_momentum_rejects_a_benchmark_without_enough_history() -> None:
    asset = _closes([100.0 + i for i in range(60)])
    recent = _closes([100.0 + i for i in range(60)]).iloc[-10:]

    with pytest.raises(InsufficientDataError, match="SPY has too little price history"):
        dual_momentum(20, 0, recent, "SPY")(asset)


def test_volatility_targeting_scales_to_the_target() -> None:
    """The position is the target over realized volatility, capped, and rebalanced rarely."""
    closes = _random_walk(400, volatility=0.02)
    realized = closes.pct_change().rolling(20).std() * np.sqrt(252)

    every_bar = volatility_targeting(0.15, 20, 2.0, 0.0, 252)(closes)
    banded = volatility_targeting(0.15, 20, 2.0, 0.2, 252)(closes)
    capped = volatility_targeting(1.0, 20, 1.0, 0.0, 252)(closes)

    assert every_bar.iloc[:20].eq(0.0).all()
    assert every_bar.iloc[20:].to_numpy() == pytest.approx((0.15 / realized).iloc[20:].to_numpy())
    assert (banded.diff().iloc[21:] != 0).sum() < (every_bar.diff().iloc[21:] != 0).sum()
    assert (banded.iloc[20:] - (0.15 / realized).iloc[20:]).abs().max() <= 0.2 + 1e-12
    assert capped.iloc[20:].eq(1.0).all()
    result = run_backtest(closes, volatility_targeting(0.15, 20, 2.0, 0.0, 252), 252)
    assert result.metrics.annualized_volatility == pytest.approx(0.15, abs=0.04)


def test_volatility_targeting_on_unmoving_closes_holds_the_cap() -> None:
    positions = volatility_targeting(0.15, 20, 1.5, 0.1, 252)(_closes([100.0] * 50))

    assert positions.iloc[:20].eq(0.0).all()
    assert positions.iloc[20:].eq(1.5).all()


def test_macd_follows_its_signal_line_after_the_warmup() -> None:
    """Long once MACD is above its signal line, flat (or short) after it crosses below."""
    closes = _closes([100.0 + i for i in range(60)] + [160.0 - 2 * i for i in range(40)])

    positions = macd(12, 26, 9)(closes)
    shorted = macd(12, 26, 9, allow_short=True)(closes)

    assert positions.iloc[:34].eq(0.0).all()
    assert positions.iloc[-1] == 0.0
    assert shorted.iloc[-1] == -1.0
    assert shorted.iloc[:34].eq(0.0).all()


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (Rsi(period=50), "50-bar RSI"),
        (BollingerBands(window=60), "60-bar Bollinger band"),
        (DonchianBreakout(entry=60, exit=10), "60-bar Donchian channel"),
        (VolatilityTargeting(window=60), "60-bar volatility window"),
        (Macd(fast=12, slow=50, signal=20), "12/50/20 MACD"),
        (DualMomentum(lookback=60), "60-bar momentum lookback"),
    ],
)
def test_new_strategies_explain_a_window_too_short_for_them(
    spec: StrategySpec, message: str
) -> None:
    """Each strategy names its own warmup when the history cannot cover it."""
    closes = _closes([100.0 + i for i in range(65)])

    with pytest.raises(InsufficientDataError, match=message):
        run_backtest(closes, strategy_for(spec, 252, closes), 252)


@pytest.mark.parametrize(
    "spec",
    [
        Rsi(allow_short=True),
        BollingerBands(),
        BollingerBands(mode="breakout", allow_short=True),
        DonchianBreakout(allow_short=True),
        DualMomentum(),
        VolatilityTargeting(max_leverage=2.0),
        Macd(allow_short=True),
    ],
)
def test_new_strategies_run_on_realistic_history(spec: StrategySpec) -> None:
    """With defaults, every strategy trades a few years of prices with finite results."""
    closes = _random_walk(1000)

    result = run_backtest(closes, strategy_for(spec, 252, _random_walk(1000, seed=1)), 252, 5.0)

    assert result.trades > 0
    assert np.isfinite(result.returns).all()
    assert 0 < result.exposure <= 2


def test_dual_momentum_needs_its_benchmark() -> None:
    with pytest.raises(ValueError, match="SPY's closes"):
        strategy_for(DualMomentum())


@pytest.mark.parametrize(
    "build",
    [
        lambda: Rsi(oversold=50),
        lambda: Rsi(overbought=100),
        lambda: BollingerBands.model_validate({"mode": "squeeze"}),
        lambda: DonchianBreakout(entry=10, exit=20),
        lambda: DualMomentum(lookback=60, skip=60),
        lambda: DualMomentum(benchmark="not a symbol"),
        lambda: VolatilityTargeting(max_leverage=3),
        lambda: Macd(fast=26, slow=12),
    ],
)
def test_new_strategy_specs_reject_invalid_parameters(build: object) -> None:
    with pytest.raises(ValidationError):
        build()  # type: ignore[operator]


def test_dual_momentum_benchmark_is_upper_cased() -> None:
    assert DualMomentum(benchmark="qqq").benchmark == "QQQ"


# ── Positions computed elsewhere ──


def _utc(closes: pd.Series) -> list[pd.Timestamp]:
    """The bar timestamps of ``closes`` as aware UTC instants."""
    return list(pd.DatetimeIndex(closes.index).tz_localize("UTC"))


def test_replayed_positions_are_held_over_the_next_bar() -> None:
    """Replayed positions get the engine's one-bar shift, like any strategy."""
    closes = _closes(JUMP)
    on_jump = [1.0 if bar == 30 else 0.0 for bar in range(len(closes))]
    before_jump = [1.0 if bar == 29 else 0.0 for bar in range(len(closes))]

    late = run_backtest(*replay_positions(closes, _utc(closes), on_jump), periods_per_year=252)
    early = run_backtest(*replay_positions(closes, _utc(closes), before_jump), periods_per_year=252)

    assert late.metrics.total_return == pytest.approx(0.0)
    assert early.metrics.total_return == pytest.approx(0.10)


def test_replay_keeps_only_bars_from_the_first_timestamp_to_the_last() -> None:
    """Bars outside the positions' range are left out, and cleaned-away bars ignored."""
    closes = _closes([100.0 + bar for bar in range(40)])
    closes.iloc[20] = 0.0
    stamps = _utc(closes)[5:35]

    window, strategy = replay_positions(closes, stamps, [0.5] * len(stamps))

    assert list(window.index) == [
        stamp for stamp in closes.index[5:35] if stamp != closes.index[20]
    ]
    assert (strategy(window) == 0.5).all()


def test_replay_requires_a_position_for_every_bar_in_range() -> None:
    """A bar inside the range without a position is an error naming its date."""
    closes = _closes([100.0] * 40)
    stamps = _utc(closes)
    del stamps[12]

    with pytest.raises(MisalignedPositionsError, match=str(closes.index[12].date())):
        replay_positions(closes, stamps, [1.0] * len(stamps))


def test_replay_outside_the_history_is_an_error() -> None:
    """Positions dated before every bar match nothing."""
    closes = _closes([100.0] * 40)
    earlier = list(pd.bdate_range("2001-01-01", periods=5, tz="UTC"))

    with pytest.raises(MisalignedPositionsError, match="No bars fall between"):
        replay_positions(closes, earlier, [1.0] * 5)
