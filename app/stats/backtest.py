"""Vectorized backtesting of position-based strategies on a single asset.

A strategy maps closes to a target position per bar: 1.0 fully long, 0.0 flat,
-1.0 fully short, or any fraction between. A target set at a bar's close is held over
the next bar, so a signal never earns the return of the bar that produced it. Each
change of position costs ``cost_bps`` per unit of turnover, and idle capital earns
nothing. Metrics come from :mod:`app.stats.metrics`, and buy-and-hold over the same
bars, without costs, is the benchmark.
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.models.backtest import BuyAndHold, PerformanceMetrics, SmaCrossover, StrategySpec
from app.stats.metrics import MIN_METRIC_RETURNS, performance_metrics
from app.stats.volatility import InsufficientDataError, clean_prices

Strategy = Callable[[pd.Series], pd.Series]
"""Maps cleaned closes to target positions on the same index."""


@dataclass(frozen=True)
class BacktestResult:
    """The outcome of a backtest.

    Return and position series start at the second bar, whose return is the first
    earned; equity curves start at the first bar, at 1.0.
    """

    returns: pd.Series
    benchmark_returns: pd.Series
    positions: pd.Series
    """Position held over each bar's return."""
    equity: pd.Series
    """Growth of one unit of capital, starting at 1.0 on the first bar."""
    benchmark_equity: pd.Series
    trades: int
    exposure: float
    metrics: PerformanceMetrics
    benchmark: PerformanceMetrics


def buy_and_hold(closes: pd.Series) -> pd.Series:
    """Be fully long on every bar."""
    return pd.Series(1.0, index=closes.index)


def sma_crossover(fast: int, slow: int, allow_short: bool = False) -> Strategy:
    """Build a strategy that is long while the ``fast``-bar average is above the ``slow`` one.

    Otherwise it is flat, or short when ``allow_short`` is set. It stays flat until the
    slow average has a full window.
    """

    def positions(closes: pd.Series) -> pd.Series:
        if len(closes) < slow + MIN_METRIC_RETURNS:
            raise InsufficientDataError(
                f"A {slow}-bar moving average leaves too few bars to trade: "
                f"{len(closes)} are available and at least {slow + MIN_METRIC_RETURNS} are "
                "needed. Request a longer period or a shorter slow window."
            )
        fast_average = closes.rolling(fast).mean()
        slow_average = closes.rolling(slow).mean()
        signal = np.where(fast_average > slow_average, 1.0, -1.0 if allow_short else 0.0)
        return pd.Series(signal, index=closes.index).where(slow_average.notna(), 0.0)

    return positions


def strategy_for(spec: StrategySpec) -> Strategy:
    """Return the strategy described by ``spec``."""
    if isinstance(spec, BuyAndHold):
        return buy_and_hold
    if isinstance(spec, SmaCrossover):
        return sma_crossover(spec.fast, spec.slow, spec.allow_short)
    raise ValueError(f"Unknown strategy {spec!r}.")


def run_backtest(
    closes: pd.Series,
    strategy: Strategy,
    periods_per_year: int,
    cost_bps: float = 0.0,
    risk_free_rate: float = 0.0,
) -> BacktestResult:
    """Backtest ``strategy`` on ``closes``.

    Args:
        closes: Adjusted close prices indexed by bar timestamp. Missing, non-finite and
            non-positive closes are dropped before the strategy sees them.
        strategy: Maps the cleaned closes to target positions.
        periods_per_year: Bars per year, used to annualize (252 for daily bars).
        cost_bps: Cost per unit of turnover, in basis points; entering a full position
            from cash is one unit, reversing from long to short is two.
        risk_free_rate: Annual risk-free rate for the Sharpe and Sortino ratios.

    Returns:
        Strategy and benchmark returns, equity curves and metrics.

    Raises:
        InsufficientDataError: If there are too few bars to backtest.
        ValueError: If the strategy returns a non-finite position.
    """
    prices = clean_prices(closes)
    if len(prices) <= MIN_METRIC_RETURNS:
        raise InsufficientDataError(
            f"A backtest needs at least {MIN_METRIC_RETURNS + 1} valid closes; only "
            f"{len(prices)} are available. Request a longer period."
        )
    targets = strategy(prices).reindex(prices.index).astype(float)
    if not np.isfinite(targets.to_numpy()).all():
        raise ValueError("The strategy returned a missing or non-finite position.")

    asset_returns = prices.pct_change().iloc[1:]
    # The target set at one close is held over the next bar; capital starts in cash.
    held = targets.shift(1, fill_value=0.0).iloc[1:]
    turnover = held.diff().fillna(held.abs()).abs()
    returns = held * asset_returns - turnover * cost_bps / 10_000.0

    inception = pd.Timestamp(prices.index[0])
    start = pd.Series([1.0], index=prices.index[:1])
    return BacktestResult(
        returns=returns,
        benchmark_returns=asset_returns,
        positions=held,
        equity=pd.concat([start, (1.0 + returns).cumprod()]),
        benchmark_equity=pd.concat([start, (1.0 + asset_returns).cumprod()]),
        trades=int((turnover > 0).sum()),
        exposure=float((held != 0).mean()),
        metrics=performance_metrics(returns, periods_per_year, risk_free_rate, inception),
        benchmark=performance_metrics(asset_returns, periods_per_year, risk_free_rate, inception),
    )
