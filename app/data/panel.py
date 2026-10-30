"""Concurrent download of adjusted closes for several symbols.

Each symbol's bars are stamped in its exchange's time zone, so they are re-dated to
that exchange's calendar date before being combined. Combining time-zone-aware indexes
directly would convert them all to UTC, shifting Asian listings back a day.
"""

import asyncio
from dataclasses import dataclass, field

import pandas as pd

from app.data.fetcher import DataFetchError, SymbolNotFoundError, fetch_price_history

MAX_CONCURRENT_DOWNLOADS: int = 8
"""Downloads in flight at once, to stay polite to the provider."""


@dataclass(frozen=True)
class ClosePanel:
    """Adjusted closes of several symbols, and the time zone each one trades in."""

    closes: pd.DataFrame
    """One column per symbol, indexed by exchange date; ``NaN`` where a symbol has no bar."""
    timezones: dict[str, str | None]
    """Each symbol's exchange time zone, e.g. ``America/New_York``; ``None`` if unknown."""
    failed: dict[str, str] = field(default_factory=dict)
    """Symbols whose download failed, with why, when failures are tolerated; they have
    no column in ``closes``."""


async def fetch_close_panel(
    symbols: list[str], period: str = "5y", interval: str = "1d", tolerate_failures: bool = False
) -> ClosePanel:
    """Fetch adjusted closes for every symbol, one column each, on the union of their dates.

    Dates are each exchange's calendar date, without time zone.

    Args:
        symbols: Distinct ticker symbols.
        period: yfinance lookback period, as for :func:`fetch_price_history`.
        interval: yfinance bar size.
        tolerate_failures: Leave out symbols that are unknown or fail to download,
            naming them in ``failed``, rather than raising, unless every one fails.

    Returns:
        Closes with a column per symbol in the order given, indexed by date, oldest
        first, with a symbol ``NaN`` on dates only the others have; and each symbol's
        exchange time zone.

    Raises:
        SymbolNotFoundError: If any symbol is unknown, naming every unknown one.
        DataFetchError: If any download fails, naming the symbols that failed.
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

    async def fetch(symbol: str) -> pd.DataFrame:
        async with semaphore:
            return await fetch_price_history(symbol, period=period, interval=interval)

    results = await asyncio.gather(*(fetch(symbol) for symbol in symbols), return_exceptions=True)

    failed = {
        symbol: "unknown symbol" if isinstance(result, SymbolNotFoundError) else str(result)
        for symbol, result in zip(symbols, results)
        if isinstance(result, DataFetchError)
    }
    if tolerate_failures and failed and len(failed) < len(symbols):
        kept = [symbol for symbol in symbols if symbol not in failed]
        panel = _combine(kept, [r for s, r in zip(symbols, results) if s not in failed])
        return ClosePanel(closes=panel.closes, timezones=panel.timezones, failed=failed)

    unknown = [s for s, r in zip(symbols, results) if isinstance(r, SymbolNotFoundError)]
    if unknown:
        raise SymbolNotFoundError(f"Unknown ticker symbols: {', '.join(unknown)}.")
    if failed:
        raise DataFetchError(f"Could not fetch price history for {', '.join(failed)}.")
    return _combine(symbols, results)


def _combine(symbols: list[str], results: list[pd.DataFrame | BaseException]) -> ClosePanel:
    """Put each symbol's downloaded closes in a column, re-dated to its exchange's dates."""
    for result in results:
        if isinstance(result, BaseException):
            raise result
    frames = {
        symbol: frame for symbol, frame in zip(symbols, results) if isinstance(frame, pd.DataFrame)
    }
    closes = {symbol: _by_exchange_date(frame["Close"]) for symbol, frame in frames.items()}
    timezones = {
        symbol: None if (tz := pd.DatetimeIndex(frame.index).tz) is None else str(tz)
        for symbol, frame in frames.items()
    }
    return ClosePanel(
        closes=pd.concat(closes, axis=1, join="outer", sort=True).reindex(columns=symbols),
        timezones=timezones,
    )


def _by_exchange_date(closes: pd.Series) -> pd.Series:
    """Index ``closes`` by the exchange's calendar date, keeping the last close of a date."""
    index = pd.DatetimeIndex(closes.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    dated = closes.set_axis(index.normalize())
    return dated[~dated.index.duplicated(keep="last")]
