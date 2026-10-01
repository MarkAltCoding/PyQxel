"""Concurrent download of adjusted closes for several symbols.

Each symbol's bars are stamped in its exchange's time zone, so they are re-dated to
that exchange's calendar date before being combined. Combining time-zone-aware indexes
directly would convert them all to UTC, shifting Asian listings back a day.
"""

import asyncio

import pandas as pd

from app.data.fetcher import DataFetchError, SymbolNotFoundError, fetch_price_history

MAX_CONCURRENT_DOWNLOADS: int = 8
"""Downloads in flight at once, to stay polite to the provider."""


async def fetch_close_panel(
    symbols: list[str], period: str = "5y", interval: str = "1d"
) -> pd.DataFrame:
    """Fetch adjusted closes for every symbol, one column each, on the union of their dates.

    Dates are each exchange's calendar date, without time zone.

    Args:
        symbols: Distinct ticker symbols.
        period: yfinance lookback period, as for :func:`fetch_price_history`.
        interval: yfinance bar size.

    Returns:
        Closes with a column per symbol in the order given, indexed by date, oldest
        first. A symbol is ``NaN`` on dates only the others have.

    Raises:
        SymbolNotFoundError: If any symbol is unknown, naming every unknown one.
        DataFetchError: If any download fails, naming the symbols that failed.
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

    async def fetch(symbol: str) -> pd.DataFrame:
        async with semaphore:
            return await fetch_price_history(symbol, period=period, interval=interval)

    results = await asyncio.gather(*(fetch(symbol) for symbol in symbols), return_exceptions=True)

    unknown = [s for s, r in zip(symbols, results) if isinstance(r, SymbolNotFoundError)]
    if unknown:
        raise SymbolNotFoundError(f"Unknown ticker symbols: {', '.join(unknown)}.")
    failed = [s for s, r in zip(symbols, results) if isinstance(r, DataFetchError)]
    if failed:
        raise DataFetchError(f"Could not fetch price history for {', '.join(failed)}.")
    for result in results:
        if isinstance(result, BaseException):
            raise result

    closes = {
        symbol: _by_exchange_date(frame["Close"])
        for symbol, frame in zip(symbols, results)
        if isinstance(frame, pd.DataFrame)
    }
    return pd.concat(closes, axis=1, join="outer", sort=True).reindex(columns=symbols)


def _by_exchange_date(closes: pd.Series) -> pd.Series:
    """Index ``closes`` by the exchange's calendar date, keeping the last close of a date."""
    index = pd.DatetimeIndex(closes.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    dated = closes.set_axis(index.normalize())
    return dated[~dated.index.duplicated(keep="last")]
