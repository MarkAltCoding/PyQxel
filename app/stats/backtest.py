"""Vectorized backtesting of position-based strategies on one asset or a portfolio.

A strategy maps closes to a target position per bar: 1.0 fully long, 0.0 flat,
-1.0 fully short, any fraction between, or up to 2.0 for volatility targeting's leverage.
A target set at a bar's close is held over the next bar, so a signal never earns the
return of the bar that produced it. Each change of position costs ``cost_bps`` per unit
of turnover. Every built-in strategy needs only adjusted closes, so it runs on any symbol
with enough history; a window too short for a strategy's warmup is an
:class:`~app.stats.volatility.InsufficientDataError` that names it.

In a portfolio the strategy runs on each holding separately, and each holding's
position is scaled by its weight; weights are reset every bar. Capital not invested
earns nothing, or, with cash interest, the risk-free rate: a holding that is flat
leaves its weight in cash, and a short's sale proceeds earn interest beside it.
Metrics come from :mod:`app.stats.metrics`, and holding the same weights over the same
bars, without costs, is the benchmark.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.models.backtest import (
    BollingerBands,
    BuyAndHold,
    DonchianBreakout,
    DualMomentum,
    Macd,
    MeanReversion,
    PerformanceMetrics,
    Rsi,
    SmaCrossover,
    StrategySpec,
    TimeSeriesMomentum,
    VolatilityTargeting,
)
from app.stats.metrics import MIN_METRIC_RETURNS, performance_metrics
from app.stats.volatility import InsufficientDataError, clean_prices

Strategy = Callable[[pd.Series], pd.Series]
"""Maps cleaned closes to target positions on the same index."""


@dataclass(frozen=True)
class AssetResult:
    """One holding's part in a backtest."""

    weight: float
    contribution: float
    """Sum of its weighted returns net of its costs."""
    exposure: float
    trades: int


@dataclass(frozen=True)
class BacktestResult:
    """The outcome of a backtest.

    Return and position series start at the second bar, whose return is the first
    earned; equity curves start at the first bar, at 1.0.
    """

    returns: pd.Series
    benchmark_returns: pd.Series
    positions: pd.DataFrame
    """Each holding's position held over each bar's return, before weighting."""
    equity: pd.Series
    """Growth of one unit of capital, starting at 1.0 on the first bar."""
    benchmark_equity: pd.Series
    trades: int
    exposure: float
    assets: dict[str, AssetResult]
    metrics: PerformanceMetrics
    benchmark: PerformanceMetrics


def _require_bars(closes: pd.Series, warmup: int, what: str) -> None:
    """Raise unless ``closes`` leaves enough bars after a ``warmup`` to trade."""
    if len(closes) < warmup + MIN_METRIC_RETURNS:
        raise InsufficientDataError(
            f"{what} leaves too few bars to trade: {len(closes)} are available and at least "
            f"{warmup + MIN_METRIC_RETURNS} are needed. Request a longer period or a shorter "
            "window."
        )


def buy_and_hold(closes: pd.Series) -> pd.Series:
    """Be fully long on every bar."""
    return pd.Series(1.0, index=closes.index)


def sma_crossover(fast: int, slow: int, allow_short: bool = False) -> Strategy:
    """Build a strategy that is long while the ``fast``-bar average is above the ``slow`` one.

    Otherwise it is flat, or short when ``allow_short`` is set. It stays flat until the
    slow average has a full window.
    """

    def positions(closes: pd.Series) -> pd.Series:
        _require_bars(closes, slow, f"A {slow}-bar moving average")
        fast_average = closes.rolling(fast).mean()
        slow_average = closes.rolling(slow).mean()
        signal = np.where(fast_average > slow_average, 1.0, -1.0 if allow_short else 0.0)
        return pd.Series(signal, index=closes.index).where(slow_average.notna(), 0.0)

    return positions


def time_series_momentum(lookback: int, skip: int = 0, allow_short: bool = False) -> Strategy:
    """Build a strategy that is long while the return from ``lookback`` to ``skip`` bars ago
    is positive.

    Otherwise it is flat, or short when ``allow_short`` is set. It stays flat until
    ``lookback`` bars have passed.
    """

    def positions(closes: pd.Series) -> pd.Series:
        _require_bars(closes, lookback, f"A {lookback}-bar momentum lookback")
        past_return = closes.shift(skip) / closes.shift(lookback) - 1.0
        signal = np.where(past_return > 0, 1.0, -1.0 if allow_short else 0.0)
        return pd.Series(signal, index=closes.index).where(past_return.notna(), 0.0)

    return positions


def _hold(
    valid: np.ndarray,
    enter_long: np.ndarray,
    exit_long: np.ndarray,
    enter_short: np.ndarray,
    exit_short: np.ndarray,
    index: pd.Index,
) -> pd.Series:
    """Positions from entry and exit signals, one boolean per bar.

    A long opens on ``enter_long`` and is held until ``exit_long``; a short likewise. A
    trade that closes may open the opposite one on the same bar. The strategy is flat
    on bars that are not ``valid``, such as a warmup.
    """
    held = np.zeros(len(index))
    position = 0.0
    for bar in range(len(index)):
        if not valid[bar]:
            position = 0.0
        else:
            if position > 0 and exit_long[bar]:
                position = 0.0
            elif position < 0 and exit_short[bar]:
                position = 0.0
            if position == 0.0:
                if enter_long[bar]:
                    position = 1.0
                elif enter_short[bar]:
                    position = -1.0
        held[bar] = position
    return pd.Series(held, index=index)


def _z_scores(closes: pd.Series, window: int) -> np.ndarray:
    """Each close's distance from its ``window``-bar average in rolling standard deviations.

    NaN during the warmup and where the closes did not move over the window.
    """
    average = closes.rolling(window).mean()
    deviation = closes.rolling(window).std(ddof=1)
    return ((closes - average) / deviation.where(deviation > 0)).to_numpy()


def mean_reversion(
    window: int,
    entry_z: float,
    exit_z: float,
    allow_short: bool = False,
    label: str = "mean-reversion window",
) -> Strategy:
    """Build a strategy that buys when the close is ``entry_z`` rolling standard deviations
    below its ``window``-bar average, and sells once it is back within ``exit_z`` of it.

    With ``allow_short`` it also shorts at ``entry_z`` above the average. A trade that
    closes may open the opposite one on the same bar if the price has crossed that far.
    ``label`` names the window in errors.
    """

    def positions(closes: pd.Series) -> pd.Series:
        _require_bars(closes, window, f"A {window}-bar {label}")
        scores = _z_scores(closes, window)
        with np.errstate(invalid="ignore"):
            return _hold(
                ~np.isnan(scores),
                enter_long=scores <= -entry_z,
                exit_long=scores >= -exit_z,
                enter_short=allow_short & (scores >= entry_z),
                exit_short=scores <= exit_z,
                index=closes.index,
            )

    return positions


def bollinger_breakout(window: int, num_std: float, allow_short: bool = False) -> Strategy:
    """Build a strategy that buys a close at or above the upper band, ``num_std`` rolling
    standard deviations over the ``window``-bar average, and sells at the average.

    With ``allow_short`` it also shorts a close at or below the lower band.
    """

    def positions(closes: pd.Series) -> pd.Series:
        _require_bars(closes, window, f"A {window}-bar Bollinger band")
        scores = _z_scores(closes, window)
        with np.errstate(invalid="ignore"):
            return _hold(
                ~np.isnan(scores),
                enter_long=scores >= num_std,
                exit_long=scores <= 0.0,
                enter_short=allow_short & (scores <= -num_std),
                exit_short=scores >= 0.0,
                index=closes.index,
            )

    return positions


def rsi(closes: pd.Series, period: int) -> pd.Series:
    """Wilder's relative strength index of ``closes``, from 0 to 100.

    NaN until ``period`` changes have passed; 50 where the closes did not move at all.
    """
    change = closes.diff()
    alpha = 1.0 / period
    gains = change.clip(lower=0.0).ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    losses = (-change).clip(lower=0.0).ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    total = gains + losses
    return (100.0 * gains / total.where(total > 0)).where(total.isna() | (total > 0), 50.0)


def rsi_reversal(
    period: int, oversold: float, overbought: float, allow_short: bool = False
) -> Strategy:
    """Build a strategy that buys at an RSI of ``oversold`` and sells at ``overbought``.

    With ``allow_short`` it also shorts at ``overbought`` and covers at ``oversold``.
    """

    def positions(closes: pd.Series) -> pd.Series:
        _require_bars(closes, period + 1, f"A {period}-bar RSI")
        strength = rsi(closes, period).to_numpy()
        with np.errstate(invalid="ignore"):
            return _hold(
                ~np.isnan(strength),
                enter_long=strength <= oversold,
                exit_long=strength >= overbought,
                enter_short=allow_short & (strength >= overbought),
                exit_short=strength <= oversold,
                index=closes.index,
            )

    return positions


def donchian_breakout(entry: int, exit: int, allow_short: bool = False) -> Strategy:
    """Build a strategy that buys a close above the previous ``entry`` bars' highest close
    and sells a close below the previous ``exit`` bars' lowest.

    With ``allow_short`` it also shorts a close below the ``entry``-bar low and covers
    above the ``exit``-bar high.
    """

    def positions(closes: pd.Series) -> pd.Series:
        _require_bars(closes, entry + 1, f"A {entry}-bar Donchian channel")
        prior = closes.shift(1)
        entry_high = prior.rolling(entry).max().to_numpy()
        entry_low = prior.rolling(entry).min().to_numpy()
        exit_high = prior.rolling(exit).max().to_numpy()
        exit_low = prior.rolling(exit).min().to_numpy()
        close = closes.to_numpy()
        with np.errstate(invalid="ignore"):
            return _hold(
                ~np.isnan(entry_high),
                enter_long=close > entry_high,
                exit_long=close < exit_low,
                enter_short=allow_short & (close < entry_low),
                exit_short=close > exit_high,
                index=closes.index,
            )

    return positions


def macd(fast: int, slow: int, signal: int, allow_short: bool = False) -> Strategy:
    """Build a strategy that is long while the MACD line is above its signal line.

    Otherwise it is flat, or short when ``allow_short`` is set. It stays flat until the
    slow average and then the signal line have had a full window each.
    """

    def positions(closes: pd.Series) -> pd.Series:
        warmup = slow + signal - 1
        _require_bars(closes, warmup, f"A {fast}/{slow}/{signal} MACD")
        line = (
            closes.ewm(span=fast, adjust=False).mean() - closes.ewm(span=slow, adjust=False).mean()
        )
        trigger = line.ewm(span=signal, adjust=False).mean()
        target = np.where(line > trigger, 1.0, -1.0 if allow_short else 0.0)
        target[:warmup] = 0.0
        return pd.Series(target, index=closes.index)

    return positions


def dual_momentum(lookback: int, skip: int, benchmark: pd.Series, name: str) -> Strategy:
    """Build a strategy that is long while the return from ``lookback`` to ``skip`` bars ago
    is positive and at least ``benchmark``'s over the same dates, and flat otherwise.

    ``benchmark`` closes are matched to each bar's date, carrying the last one over
    holidays the two markets do not share. The strategy is flat where the benchmark has
    no price yet. ``name`` labels the benchmark in errors.
    """

    def positions(closes: pd.Series) -> pd.Series:
        _require_bars(closes, lookback, f"A {lookback}-bar momentum lookback")
        dates = _calendar_dates(closes.index)
        reference = clean_prices(benchmark)
        reference.index = _calendar_dates(reference.index)
        reference = reference[~reference.index.duplicated(keep="last")]
        aligned = reference.reindex(reference.index.union(dates)).ffill().loc[dates]
        aligned.index = closes.index
        if aligned.notna().sum() <= lookback:
            raise InsufficientDataError(
                f"{name} has too little price history over this window to compare "
                f"{lookback}-bar returns with. Request a longer period or a shorter lookback."
            )
        own = closes.shift(skip) / closes.shift(lookback) - 1.0
        relative = aligned.shift(skip) / aligned.shift(lookback) - 1.0
        signal = np.where((own > 0) & (own >= relative), 1.0, 0.0)
        return pd.Series(signal, index=closes.index).where(own.notna() & relative.notna(), 0.0)

    return positions


def volatility_targeting(
    target_volatility: float,
    window: int,
    max_leverage: float,
    rebalance_threshold: float,
    periods_per_year: int,
) -> Strategy:
    """Build a long strategy sized to ``target_volatility`` over the last ``window`` returns.

    The target is capped at ``max_leverage``, and the position held changes only when the
    target moves more than ``rebalance_threshold`` from it. Where the closes did not move,
    the target is the cap. It stays flat until ``window`` returns have passed.
    """

    def positions(closes: pd.Series) -> pd.Series:
        _require_bars(closes, window + 1, f"A {window}-bar volatility window")
        realized = closes.pct_change().rolling(window).std(ddof=1) * math.sqrt(periods_per_year)
        capped = (target_volatility / realized.where(realized > 0)).clip(upper=max_leverage)
        targets = capped.where(realized.isna() | (realized > 0), max_leverage).to_numpy()
        held = np.zeros(len(targets))
        position = 0.0
        for bar, target in enumerate(targets):
            if math.isnan(target):
                position = 0.0
            elif position == 0.0 or abs(target - position) > rebalance_threshold:
                position = float(target)
            held[bar] = position
        return pd.Series(held, index=closes.index)

    return positions


def strategy_for(
    spec: StrategySpec, periods_per_year: int = 252, benchmark: pd.Series | None = None
) -> Strategy:
    """Return the strategy described by ``spec``.

    Args:
        spec: The strategy and its parameters.
        periods_per_year: Bars per year, used to annualize volatility targets.
        benchmark: Closes of ``spec.benchmark`` on the same bar size, for dual momentum.

    Raises:
        ValueError: If dual momentum is asked for without its benchmark's closes.
    """
    if isinstance(spec, BuyAndHold):
        return buy_and_hold
    if isinstance(spec, SmaCrossover):
        return sma_crossover(spec.fast, spec.slow, spec.allow_short)
    if isinstance(spec, TimeSeriesMomentum):
        return time_series_momentum(spec.lookback, spec.skip, spec.allow_short)
    if isinstance(spec, MeanReversion):
        return mean_reversion(spec.window, spec.entry_z, spec.exit_z, spec.allow_short)
    if isinstance(spec, Rsi):
        return rsi_reversal(spec.period, spec.oversold, spec.overbought, spec.allow_short)
    if isinstance(spec, BollingerBands):
        if spec.mode == "breakout":
            return bollinger_breakout(spec.window, spec.num_std, spec.allow_short)
        return mean_reversion(
            spec.window, spec.num_std, 0.0, spec.allow_short, label="Bollinger band"
        )
    if isinstance(spec, DonchianBreakout):
        return donchian_breakout(spec.entry, spec.exit, spec.allow_short)
    if isinstance(spec, DualMomentum):
        if benchmark is None:
            raise ValueError(f"Dual momentum needs {spec.benchmark}'s closes.")
        return dual_momentum(spec.lookback, spec.skip, benchmark, spec.benchmark)
    if isinstance(spec, VolatilityTargeting):
        return volatility_targeting(
            spec.target_volatility,
            spec.window,
            spec.max_leverage,
            spec.rebalance_threshold,
            periods_per_year,
        )
    if isinstance(spec, Macd):
        return macd(spec.fast, spec.slow, spec.signal, spec.allow_short)
    raise ValueError(f"Unknown strategy {spec!r}.")


def _calendar_dates(index: pd.Index) -> pd.DatetimeIndex:
    """Bar timestamps as calendar dates without time zone."""
    dates = pd.DatetimeIndex(index)
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    return dates.normalize()


def cash_returns(risk_free: pd.Series, bars: pd.Index) -> pd.Series:
    """The risk-free return over each bar, compounding the daily rate between bar dates.

    Args:
        risk_free: Daily risk-free returns as decimals, indexed by date.
        bars: Bar timestamps, oldest first.

    Returns:
        The return of cash from each bar's close to the next, on ``bars[1:]``. Days after
        the risk-free data ends earn its last published rate.
    """
    daily = risk_free.dropna().sort_index()
    daily.index = _calendar_dates(daily.index)
    dates = _calendar_dates(bars)
    if daily.empty:
        return pd.Series(0.0, index=bars[1:])
    if dates[-1] > daily.index[-1]:
        later = pd.bdate_range(daily.index[-1] + pd.Timedelta(days=1), dates[-1])
        daily = pd.concat([daily, pd.Series(float(daily.iloc[-1]), index=later)])
    growth = (1.0 + daily).cumprod()
    level = growth.reindex(growth.index.union(dates)).ffill().fillna(1.0).loc[dates]
    per_bar = level.to_numpy()[1:] / level.to_numpy()[:-1] - 1.0
    return pd.Series(per_bar, index=bars[1:])


def _targets(prices: pd.DataFrame, strategy: Strategy) -> pd.DataFrame:
    """Each holding's target positions, checked to be finite."""
    targets = pd.DataFrame(
        {
            symbol: strategy(prices[symbol]).reindex(prices.index).astype(float)
            for symbol in prices.columns
        }
    )
    if not np.isfinite(targets.to_numpy()).all():
        raise ValueError("The strategy returned a missing or non-finite position.")
    return targets


def run_portfolio_backtest(
    prices: pd.DataFrame,
    weights: dict[str, float],
    strategy: Strategy,
    periods_per_year: int,
    cost_bps: float = 0.0,
    risk_free_rate: float = 0.0,
    cash: pd.Series | None = None,
) -> BacktestResult:
    """Backtest ``strategy`` on every holding of a portfolio and combine them by weight.

    Args:
        prices: Adjusted closes, one column per holding, on the bars they all traded,
            oldest first.
        weights: Each holding's share of capital; they should sum to one.
        strategy: Maps one holding's closes to its target positions.
        periods_per_year: Bars per year, used to annualize (252 for daily bars).
        cost_bps: Cost per unit of turnover, in basis points; entering a full position
            from cash is one unit, reversing from long to short is two.
        risk_free_rate: Annual risk-free rate for the Sharpe and Sortino ratios.
        cash: The risk-free return over each bar after the first, from
            :func:`cash_returns`; capital not invested earns it. Omit to earn nothing.

    Returns:
        Portfolio and benchmark returns, equity curves, each holding's part, and metrics.

    Raises:
        InsufficientDataError: If there are too few bars for the strategy.
        ValueError: If the strategy returns a non-finite position.
    """
    if len(prices) <= MIN_METRIC_RETURNS:
        raise InsufficientDataError(
            f"A backtest needs at least {MIN_METRIC_RETURNS + 1} valid closes; only "
            f"{len(prices)} are available. Request a longer period."
        )
    weight = pd.Series(weights).reindex(prices.columns).astype(float)
    asset_returns = prices.pct_change().iloc[1:]
    # The target set at one close is held over the next bar; capital starts in cash.
    held = _targets(prices, strategy).shift(1, fill_value=0.0).iloc[1:]
    turnover = held.diff().fillna(held.abs()).abs()
    earned = held.mul(weight, axis=1) * asset_returns - turnover.mul(weight, axis=1) * (
        cost_bps / 10_000.0
    )
    returns = earned.sum(axis=1)
    if cash is not None:
        idle = 1.0 - held.mul(weight, axis=1).sum(axis=1)
        returns = returns + idle * cash.reindex(returns.index).fillna(0.0)
    benchmark_returns = asset_returns.mul(weight, axis=1).sum(axis=1)

    inception = pd.Timestamp(prices.index[0])
    start = pd.Series([1.0], index=prices.index[:1])
    changed = turnover > 0
    return BacktestResult(
        returns=returns,
        benchmark_returns=benchmark_returns,
        positions=held,
        equity=pd.concat([start, (1.0 + returns).cumprod()]),
        benchmark_equity=pd.concat([start, (1.0 + benchmark_returns).cumprod()]),
        trades=int(changed.any(axis=1).sum()),
        exposure=float(held.abs().mul(weight, axis=1).sum(axis=1).mean()),
        assets={
            symbol: AssetResult(
                weight=float(weight[symbol]),
                contribution=float(earned[symbol].sum()),
                exposure=float((held[symbol] != 0).mean()),
                trades=int(changed[symbol].sum()),
            )
            for symbol in prices.columns
        },
        metrics=performance_metrics(returns, periods_per_year, risk_free_rate, inception),
        benchmark=performance_metrics(
            benchmark_returns, periods_per_year, risk_free_rate, inception
        ),
    )


def run_backtest(
    closes: pd.Series,
    strategy: Strategy,
    periods_per_year: int,
    cost_bps: float = 0.0,
    risk_free_rate: float = 0.0,
    risk_free: pd.Series | None = None,
) -> BacktestResult:
    """Backtest ``strategy`` on one asset's ``closes``.

    Args:
        closes: Adjusted close prices indexed by bar timestamp. Missing, non-finite and
            non-positive closes are dropped before the strategy sees them.
        strategy: Maps the cleaned closes to target positions.
        periods_per_year: Bars per year, used to annualize (252 for daily bars).
        cost_bps: Cost per unit of turnover, in basis points.
        risk_free_rate: Annual risk-free rate for the Sharpe and Sortino ratios.
        risk_free: Daily risk-free returns indexed by date; capital not invested earns
            them. Omit to earn nothing.

    Returns:
        Strategy and benchmark returns, equity curves and metrics.

    Raises:
        InsufficientDataError: If there are too few bars to backtest.
        ValueError: If the strategy returns a non-finite position.
    """
    prices = clean_prices(closes)
    cash = None if risk_free is None else cash_returns(risk_free, prices.index)
    return run_portfolio_backtest(
        prices.to_frame("asset"),
        {"asset": 1.0},
        strategy,
        periods_per_year,
        cost_bps=cost_bps,
        risk_free_rate=risk_free_rate,
        cash=cash,
    )
