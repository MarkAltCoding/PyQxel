"""Cross-sectional backtests: rank a universe of stocks against one another and hold the best.

At the last close of every week, month or quarter, each stock with enough history is
scored by a signal such as its trailing return, and the universe is ranked by it. The
top stocks are bought, and with ``long_short`` the same number of the bottom ones are
shorted, each leg one unit of capital. Between rebalances the weights drift with prices,
and each rebalance pays ``cost_bps`` on the capital it trades. Holding every stock
equally, rebalanced daily and without costs, is the benchmark.

Stocks list and delist at different times, so the universe is not cut to the dates
every stock traded, as a portfolio is. Dates on which fewer than half of the stocks
then listed traded are dropped, as holidays of a minority of markets, and a stock's last
close is carried over the gaps in its own history. A stock enters the ranking once its
signal has a full window, and is never ranked before its first close or after its last.
"""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.models.backtest import (
    CrossSectionalMomentum,
    DistanceFromAverage,
    LegWeighting,
    LowVolatility,
    PerformanceMetrics,
    RankingSignal,
    RebalanceFrequency,
    RiskAdjustedMomentum,
    ShortTermReversal,
)
from app.stats.backtest import cash_returns
from app.stats.metrics import MIN_METRIC_RETURNS, performance_metrics
from app.stats.volatility import InsufficientDataError

MIN_RANKED: int = 3
"""Fewest stocks with a score needed to rank the universe at a rebalance."""

MIN_TRADING_SHARE: float = 0.5
"""Share of the listed stocks that must trade on a date for it to be kept."""

MIN_IC_STOCKS: int = 3
"""Fewest stocks with a score and a next-period return needed to measure an IC."""

PERIOD_CODES: dict[RebalanceFrequency, str] = {"weekly": "W-FRI", "monthly": "M", "quarterly": "Q"}
"""pandas period codes of each rebalance frequency."""


@dataclass(frozen=True)
class RankedStock:
    """One stock's part in a cross-sectional backtest."""

    contribution: float
    """Sum of its weighted returns, before costs."""
    long_share: float
    short_share: float
    first_ranked: pd.Timestamp | None
    score: float | None
    """Its signal at the last rebalance; ``None`` if it was not ranked."""
    weight: float
    """Its target weight at the last rebalance, negative when short."""


@dataclass(frozen=True)
class RankIC:
    """The rank information coefficient over a backtest's rebalances."""

    mean: float
    t_stat: float | None
    hit_rate: float
    observations: int


@dataclass(frozen=True)
class CrossSectionalResult:
    """The outcome of a cross-sectional backtest.

    Return series start the bar after the first rebalance; equity curves start at that
    rebalance, at 1.0.
    """

    returns: pd.Series
    benchmark_returns: pd.Series
    equity: pd.Series
    benchmark_equity: pd.Series
    rebalances: int
    holdings_per_leg: float
    turnover: float
    exposure: float
    information_coefficient: RankIC | None
    assets: dict[str, RankedStock]
    metrics: PerformanceMetrics
    benchmark: PerformanceMetrics


def _by_date(closes: pd.DataFrame) -> pd.DataFrame:
    """Closes indexed by calendar date without time zone, keeping valid prices only."""
    frame = closes.apply(pd.to_numeric, errors="coerce").astype(float)
    index = pd.DatetimeIndex(frame.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    frame.index = index.normalize()
    frame = frame.groupby(level=0).last().sort_index()
    valid: pd.DataFrame = frame.where(np.isfinite(frame) & (frame > 0))
    return valid


def universe_prices(closes: pd.DataFrame) -> pd.DataFrame:
    """Align a universe's closes on one calendar, carrying each stock's last close over the
    gaps in its own history.

    Args:
        closes: Adjusted closes, one column per stock, indexed by bar timestamp; missing,
            non-finite and non-positive values count as no close that day.

    Returns:
        Closes on the dates at least :data:`MIN_TRADING_SHARE` of the stocks then listed
        traded, ``NaN`` before a stock's first close and after its last.
    """
    prices = _by_date(closes)
    traded = prices.notna()
    # A stock is listed from its first close to its last.
    listed = traded.cummax() & traded[::-1].cummax()[::-1]
    share = traded.sum(axis=1) / listed.sum(axis=1).where(listed.any(axis=1))
    keep = share >= MIN_TRADING_SHARE
    filled: pd.DataFrame = prices[keep].ffill().where(listed[keep])
    return filled


def signal_warmup(signal: RankingSignal) -> int:
    """Bars a stock needs before ``signal`` can score it."""
    if isinstance(signal, (CrossSectionalMomentum, RiskAdjustedMomentum, ShortTermReversal)):
        return signal.lookback + 1
    if isinstance(signal, LowVolatility):
        return signal.window + 1
    return signal.window


def signal_scores(prices: pd.DataFrame, signal: RankingSignal) -> pd.DataFrame:
    """Score every stock on every date by ``signal``, higher meaning more wanted.

    Args:
        prices: Aligned closes from :func:`universe_prices`.
        signal: The ranking signal.

    Returns:
        Scores on the same index and columns; ``NaN`` where a stock lacks the history.
    """
    returns = prices.pct_change(fill_method=None)
    if isinstance(signal, CrossSectionalMomentum):
        scores = prices.shift(signal.skip) / prices.shift(signal.lookback) - 1.0
    elif isinstance(signal, ShortTermReversal):
        scores = -(prices / prices.shift(signal.lookback) - 1.0)
    elif isinstance(signal, LowVolatility):
        scores = -returns.rolling(signal.window).std(ddof=1)
    elif isinstance(signal, DistanceFromAverage):
        scores = prices / prices.rolling(signal.window).mean() - 1.0
    else:
        span = signal.lookback - signal.skip
        past = prices.shift(signal.skip) / prices.shift(signal.lookback) - 1.0
        volatility = returns.shift(signal.skip).rolling(span).std(ddof=1) * math.sqrt(span)
        scores = past / volatility.where(volatility > 0)
    finite: pd.DataFrame = scores.where(np.isfinite(scores) & prices.notna())
    return finite


def rebalance_bars(dates: pd.DatetimeIndex, frequency: RebalanceFrequency) -> list[int]:
    """Positions of the last date of each week, month or quarter, but the final date's."""
    periods = dates.to_period(PERIOD_CODES[frequency])
    return [bar for bar in range(len(dates) - 1) if periods[bar] != periods[bar + 1]]


def _targets(
    scores: np.ndarray,
    volatility: np.ndarray | None,
    quantile: float,
    top_n: int | None,
    long_short: bool,
) -> tuple[np.ndarray, int] | None:
    """Target weights from one date's scores, and the stocks in each leg.

    Ties go to the stock listed first. ``None`` when fewer than :data:`MIN_RANKED` stocks
    can be ranked.
    """
    eligible = np.isfinite(scores)
    if volatility is not None:
        eligible &= np.isfinite(volatility) & (volatility > 0)
    count = int(eligible.sum())
    if count < MIN_RANKED:
        return None
    size = top_n if top_n is not None else max(1, math.floor(quantile * count + 1e-9))
    size = min(size, count // 2 if long_short else count)
    candidates = np.flatnonzero(eligible)
    order = candidates[np.lexsort((candidates, -scores[candidates]))]
    weights = np.zeros(len(scores))

    def fill(leg: np.ndarray, sign: float) -> None:
        if volatility is None:
            weights[leg] = sign / len(leg)
        else:
            inverse = 1.0 / volatility[leg]
            weights[leg] = sign * inverse / inverse.sum()

    fill(order[:size], 1.0)
    if long_short:
        fill(order[-size:], -1.0)
    return weights, size


def _rank_ic(rankings: list[tuple[np.ndarray, np.ndarray]]) -> RankIC | None:
    """The Spearman correlation of each ranking's scores with the returns that followed."""
    values: list[float] = []
    for scores, forward in rankings:
        both = np.isfinite(scores) & np.isfinite(forward)
        if both.sum() < MIN_IC_STOCKS:
            continue
        ranked_scores = pd.Series(scores[both]).rank()
        ranked_returns = pd.Series(forward[both]).rank()
        if ranked_scores.nunique() < 2 or ranked_returns.nunique() < 2:
            continue
        values.append(float(np.corrcoef(ranked_scores, ranked_returns)[0, 1]))
    if len(values) < 2:
        return None
    series = np.array(values)
    deviation = float(series.std(ddof=1))
    mean = float(np.clip(series.mean(), -1.0, 1.0))
    return RankIC(
        mean=mean,
        t_stat=mean / (deviation / math.sqrt(len(series))) if deviation > 0 else None,
        hit_rate=float((series > 0).mean()),
        observations=len(series),
    )


def run_cross_sectional_backtest(
    closes: pd.DataFrame,
    signal: RankingSignal,
    rebalance: RebalanceFrequency = "monthly",
    quantile: float = 0.2,
    top_n: int | None = None,
    long_short: bool = False,
    weighting: LegWeighting = "equal",
    volatility_window: int = 63,
    cost_bps: float = 0.0,
    risk_free_rate: float = 0.0,
    risk_free: pd.Series | None = None,
    periods_per_year: int = 252,
) -> CrossSectionalResult:
    """Rank ``closes``' stocks by ``signal`` on a schedule and backtest holding the best.

    Args:
        closes: Daily adjusted closes, one column per stock, indexed by date; ``NaN`` where
            a stock has no close.
        signal: What the stocks are ranked by.
        rebalance: How often they are ranked again.
        quantile: Share of the ranked stocks in each leg, at least one.
        top_n: Stocks in each leg, instead of ``quantile``.
        long_short: Also short the worst-ranked stocks, as many as are bought.
        weighting: Split each leg equally, or by inverse volatility.
        volatility_window: Daily returns inverse-volatility weights are measured over.
        cost_bps: Cost per unit of capital traded, in basis points.
        risk_free_rate: Annual risk-free rate for the Sharpe and Sortino ratios.
        risk_free: Daily risk-free returns indexed by date; capital not invested and short
            sale proceeds earn them. Omit to earn nothing.
        periods_per_year: Bars per year, used to annualize.

    Returns:
        Strategy and benchmark returns, equity curves, each stock's part, and metrics.

    Raises:
        InsufficientDataError: If no rebalance has enough stocks with a score, or too few
            bars follow the first one.
    """
    prices = universe_prices(closes)
    symbols = [str(symbol) for symbol in prices.columns]
    dates = pd.DatetimeIndex(prices.index)
    scores = signal_scores(prices, signal).to_numpy()
    returns = prices.pct_change(fill_method=None).to_numpy()
    volatility = (
        prices.pct_change(fill_method=None).rolling(volatility_window).std(ddof=1).to_numpy()
        if weighting == "inverse_volatility"
        else None
    )
    candidates = rebalance_bars(dates, rebalance)
    rankings = {
        bar: ranked
        for bar in candidates
        if (
            ranked := _targets(
                scores[bar],
                None if volatility is None else volatility[bar],
                quantile,
                top_n,
                long_short,
            )
        )
        is not None
    }
    if not rankings:
        raise InsufficientDataError(
            f"No rebalance had {MIN_RANKED} stocks with enough history to rank: the signal "
            f"needs {signal_warmup(signal)} daily closes of each. Request a longer period or "
            "a shorter window."
        )
    start = min(rankings)
    if len(dates) - 1 - start < MIN_METRIC_RETURNS:
        raise InsufficientDataError(
            f"Only {len(dates) - 1 - start} daily returns follow the first rebalance, on "
            f"{dates[start].date()}; at least {MIN_METRIC_RETURNS} are needed. Request a "
            "longer period or a shorter window."
        )
    cash = (
        np.zeros(len(dates))
        if risk_free is None
        else np.concatenate([[0.0], cash_returns(risk_free, dates).to_numpy()])
    )

    count = len(symbols)
    weights = np.zeros(count)
    contributions = np.zeros(count)
    long_counts = np.zeros(count)
    short_counts = np.zeros(count)
    first_ranked: list[pd.Timestamp | None] = [None] * count
    pending_cost = 0.0
    earned: list[float] = []
    benchmark: list[float] = []
    traded: list[float] = []
    gross: list[float] = []
    leg_sizes: list[int] = []
    scored: list[tuple[int, np.ndarray]] = []
    last_target = np.zeros(count)
    for bar in range(start, len(dates)):
        if bar > start:
            bar_returns = np.nan_to_num(returns[bar], nan=0.0)
            gross.append(float(np.abs(weights).sum()))
            contributions += weights * bar_returns
            growth = (
                1.0 + float(weights @ bar_returns) + (1.0 - float(weights.sum())) * float(cash[bar])
            )
            # The last rebalance's cost came out of capital before this bar's return.
            earned.append((1.0 - pending_cost) * growth - 1.0)
            available = returns[bar][np.isfinite(returns[bar])]
            benchmark.append(float(available.mean()) if len(available) else 0.0)
            if growth > 0:
                weights = weights * (1.0 + bar_returns) / growth
            pending_cost = 0.0
        if bar in rankings:
            target, size = rankings[bar]
            traded.append(float(np.abs(target - weights).sum()))
            pending_cost = traded[-1] * cost_bps / 10_000.0
            weights = target
            last_target = target
            long_counts += target > 0
            short_counts += target < 0
            leg_sizes.append(size)
            scored.append((bar, scores[bar]))
            for column in np.flatnonzero(np.isfinite(scores[bar])):
                if first_ranked[column] is None:
                    first_ranked[column] = pd.Timestamp(dates[bar])

    # Each ranking against the returns to the next rebalance, or to the last close.
    price_matrix = prices.to_numpy()
    ends = [bar for bar, _ in scored[1:]] + [len(dates) - 1]
    information = _rank_ic(
        [
            (bar_scores, price_matrix[end] / price_matrix[bar] - 1.0)
            for (bar, bar_scores), end in zip(scored, ends)
        ]
    )

    index = dates[start + 1 :]
    strategy_returns = pd.Series(earned, index=index)
    benchmark_returns = pd.Series(benchmark, index=index)
    first = pd.Series([1.0], index=dates[start : start + 1])
    inception = pd.Timestamp(dates[start])
    rebalances = len(scored)
    final_scores = scored[-1][1]
    return CrossSectionalResult(
        returns=strategy_returns,
        benchmark_returns=benchmark_returns,
        equity=pd.concat([first, (1.0 + strategy_returns).cumprod()]),
        benchmark_equity=pd.concat([first, (1.0 + benchmark_returns).cumprod()]),
        rebalances=rebalances,
        holdings_per_leg=float(np.mean(leg_sizes)),
        turnover=float(np.mean(traded)),
        exposure=float(np.mean(gross)),
        information_coefficient=information,
        assets={
            symbol: RankedStock(
                contribution=float(contributions[column]),
                long_share=float(long_counts[column] / rebalances),
                short_share=float(short_counts[column] / rebalances),
                first_ranked=first_ranked[column],
                score=(
                    float(final_scores[column]) if math.isfinite(final_scores[column]) else None
                ),
                weight=float(last_target[column]),
            )
            for column, symbol in enumerate(symbols)
        },
        metrics=performance_metrics(strategy_returns, periods_per_year, risk_free_rate, inception),
        benchmark=performance_metrics(
            benchmark_returns, periods_per_year, risk_free_rate, inception
        ),
    )
