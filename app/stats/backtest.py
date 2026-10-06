"""Vectorized backtesting of position-based strategies on one asset or a portfolio.

A strategy maps closes to a target position per bar: 1.0 fully long, 0.0 flat,
-1.0 fully short, or any fraction between. A target set at a bar's close is held over
the next bar, so a signal never earns the return of the bar that produced it. Each
change of position costs ``cost_bps`` per unit of turnover.

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
    BuyAndHold,
    MeanReversion,
    PerformanceMetrics,
    SmaCrossover,
    StrategySpec,
    TimeSeriesMomentum,
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


def mean_reversion(
    window: int, entry_z: float, exit_z: float, allow_short: bool = False
) -> Strategy:
    """Build a strategy that buys when the close is ``entry_z`` rolling standard deviations
    below its ``window``-bar average, and sells once it is back within ``exit_z`` of it.

    With ``allow_short`` it also shorts at ``entry_z`` above the average. A trade that
    closes may open the opposite one on the same bar if the price has crossed that far.
    """

    def positions(closes: pd.Series) -> pd.Series:
        _require_bars(closes, window, f"A {window}-bar mean-reversion window")
        average = closes.rolling(window).mean()
        deviation = closes.rolling(window).std(ddof=1)
        scores = ((closes - average) / deviation.where(deviation > 0)).to_numpy()
        held = np.zeros(len(scores))
        position = 0.0
        for bar, score in enumerate(scores):
            if math.isnan(score):
                position = 0.0
            else:
                if position > 0 and score >= -exit_z:
                    position = 0.0
                elif position < 0 and score <= exit_z:
                    position = 0.0
                if position == 0.0:
                    if score <= -entry_z:
                        position = 1.0
                    elif allow_short and score >= entry_z:
                        position = -1.0
            held[bar] = position
        return pd.Series(held, index=closes.index)

    return positions


def strategy_for(spec: StrategySpec) -> Strategy:
    """Return the strategy described by ``spec``."""
    if isinstance(spec, BuyAndHold):
        return buy_and_hold
    if isinstance(spec, SmaCrossover):
        return sma_crossover(spec.fast, spec.slow, spec.allow_short)
    if isinstance(spec, TimeSeriesMomentum):
        return time_series_momentum(spec.lookback, spec.skip, spec.allow_short)
    if isinstance(spec, MeanReversion):
        return mean_reversion(spec.window, spec.entry_z, spec.exit_z, spec.allow_short)
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
