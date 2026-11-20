"""How a stored list of Claude's picks has done since it was made.

A list is measured as if bought at the last close before it was made, in Claude's
weights, and held without rebalancing: each pick's value grows with its own price, so
the list's return is each pick's gain times its starting weight. Its benchmark is
measured over the same days. Measuring stops when the holding period ends, so a list's
record is frozen then and stays as it was. Nothing here fetches prices.
"""

import math
from datetime import date, datetime, time, timedelta
from typing import cast
from zoneinfo import ZoneInfo

import pandas as pd

from app.models.picks import (
    BENCHMARK_NAMES,
    PerformancePoint,
    PickPerformance,
    PicksBenchmark,
    PicksPerformance,
    PicksResponse,
)

MARKET_TIME = ZoneInfo("America/New_York")
"""Where US exchanges keep time."""

MARKET_CLOSE = time(16, 0)
"""When US exchanges close; a list made before then that day could not have bought at that
day's close."""

MAX_POINTS: int = 400
"""Most points the series is thinned to, keeping its last."""


def start_day(generated_at: datetime) -> date:
    """The last trading day whose close came before ``generated_at``: that day itself
    after the close, else the day before (an earlier trading day is found in the prices)."""
    local = generated_at.astimezone(MARKET_TIME)
    return local.date() if local.time() >= MARKET_CLOSE else local.date() - timedelta(days=1)


def ends_on(picks: PicksResponse) -> date:
    """The day the list's holding period ends."""
    return (picks.generated_at + timedelta(weeks=picks.context.horizon_weeks)).date()


def _finite(value: object) -> float | None:
    if value is None:
        return None
    number = float(value)  # type: ignore[arg-type]
    return number if math.isfinite(number) else None


def _start_index(series: pd.Series, start: date) -> pd.Timestamp | None:
    """The date of ``series``'s close the list is measured from: its last on or before
    ``start``, else (a security that began trading later) its first after it."""
    known = series.dropna()
    if known.empty:
        return None
    before = known[known.index <= pd.Timestamp(start)]
    found = before.index[-1] if not before.empty else known.index[0]
    return pd.Timestamp(found)


def track(
    picks: PicksResponse, closes: pd.DataFrame, benchmark: PicksBenchmark, today: date
) -> PicksPerformance:
    """How ``picks`` have done, from ``closes`` (adjusted closes, a column per symbol and
    the benchmark's, indexed by date), up to ``today`` or the end of the holding period."""
    assert picks.id is not None
    start = start_day(picks.generated_at)
    finish = min(today, ends_on(picks))
    ended = today > ends_on(picks)
    window = closes[closes.index <= pd.Timestamp(finish)] if not closes.empty else closes
    performances: list[PickPerformance] = []
    growth: dict[str, pd.Series] = {}
    missing: list[str] = []
    for pick in picks.report.picks:
        series = window[pick.symbol] if pick.symbol in window.columns else pd.Series(dtype=float)
        first = _start_index(series, start)
        if first is None:
            missing.append(pick.symbol)
            performances.append(PickPerformance(symbol=pick.symbol, weight=pick.weight))
            continue
        base = float(series.loc[first])
        relative = (series[series.index >= first] / base).ffill()
        growth[pick.symbol] = relative
        latest = _finite(series.dropna().iloc[-1])
        gain = None if latest is None else latest / base - 1
        performances.append(
            PickPerformance(
                symbol=pick.symbol,
                weight=pick.weight,
                start_price=round(base, 4),
                latest_price=None if latest is None else round(latest, 4),
                gain=None if gain is None else round(gain, 6),
            )
        )
    priced_weight = sum(p.weight for p in performances if p.gain is not None)
    for performance in performances:
        if performance.gain is not None and priced_weight > 0:
            performance.contribution = round(
                performance.weight / priced_weight * performance.gain, 6
            )
    total = (
        round(sum(p.contribution or 0.0 for p in performances), 6) if priced_weight > 0 else None
    )

    bench = window[benchmark] if benchmark in window.columns else pd.Series(dtype=float)
    bench_first = _start_index(bench, start)
    bench_growth = (
        None
        if bench_first is None
        else (bench[bench.index >= bench_first] / float(bench.loc[bench_first])).ffill()
    )
    bench_return = None if bench_growth is None else _finite(bench_growth.dropna().iloc[-1] - 1)

    series_points: list[PerformancePoint] = []
    if growth:
        frame = pd.DataFrame(growth).sort_index().ffill()
        weights = pd.Series(
            {p.symbol: p.weight / priced_weight for p in performances if p.gain is not None}
        )
        value = frame.fillna(1.0).mul(weights, axis=1).sum(axis=1) - 1
        joined = pd.DataFrame({"picks": value})
        if bench_growth is not None:
            joined["benchmark"] = bench_growth - 1
            joined = joined.sort_index().ffill()
        step = max(1, math.ceil(len(joined) / MAX_POINTS))
        thinned = joined.iloc[::step]
        if len(joined) and thinned.index[-1] != joined.index[-1]:
            thinned = pd.concat([thinned, joined.iloc[[-1]]])
        series_points = [
            PerformancePoint(
                date=cast(pd.Timestamp, day).date(),
                picks=_finite(round(row["picks"], 6)),
                benchmark=_finite(round(row["benchmark"], 6)) if "benchmark" in row else None,
            )
            for day, row in thinned.iterrows()
        ]

    first_days = [g.index[0] for g in growth.values()]
    notices = []
    if missing:
        notices.append(
            f"No prices for {', '.join(missing)}: the list's return counts the other picks, "
            "their weights scaled up to fill its share."
        )
    if bench_return is None:
        notices.append(f"{BENCHMARK_NAMES[benchmark]} could not be priced.")
    return PicksPerformance(
        id=picks.id,
        benchmark=benchmark,
        benchmark_name=BENCHMARK_NAMES[benchmark],
        start=pd.Timestamp(min(first_days)).date() if first_days else None,
        as_of=series_points[-1].date if series_points else None,
        ends_on=ends_on(picks),
        ended=ended,
        total_return=total,
        benchmark_return=None if bench_return is None else round(bench_return, 6),
        excess_return=(
            None if total is None or bench_return is None else round(total - bench_return, 6)
        ),
        picks=performances,
        series=series_points,
        notice=" ".join(notices) or None,
    )


def history_period(oldest: date, today: date) -> str:
    """The shortest lookback of daily bars that reaches back before ``oldest``."""
    days = (today - oldest).days + 10
    for period, length in (("1mo", 28), ("3mo", 88), ("6mo", 180), ("1y", 362), ("2y", 727)):
        if days <= length:
            return period
    return "5y" if days <= 1820 else "10y"
