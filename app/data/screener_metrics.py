"""Market data for a user's own screener, fetched on that user's keys by their nightly
refresh.

Finnhub's free basic financials give every listed stock's market cap, valuation, margins,
growth and 26-week return, one request per stock. Price-history columns (momentum,
volatility, drawdown, factor betas) need daily prices, fetched through
:func:`app.data.fetcher.fetch_price_history` on whichever price-history key the user has,
cheapest first. Free plans allow only some stocks a night, so the largest are fetched
first and the pass stops once the keys' limits are reached.

Nothing here falls back to yfinance: what the user sees in their universe comes from
their own licensed keys and the SEC's public data.
"""

import asyncio
import logging
import math
from collections.abc import Callable, Iterable
from datetime import date
from typing import Any, TypeVar

import httpx2
import pandas as pd

from app.data.fetcher import (
    HTTP_TIMEOUT_SECONDS,
    DataFetchError,
    FinnhubRateLimitError,
    ProviderNotConfiguredError,
    fetch_price_history,
    finnhub_key,
    finnhub_symbol,
    finnhub_get,
)
from app.data.universe import RequestPacer
from app.models.screener import ProviderScreenMetrics

logger = logging.getLogger(__name__)

FINNHUB_NIGHTLY_PER_MINUTE: int = 50
"""Finnhub requests the nightly refresh sends a minute, under the free plan's 60."""

FINNHUB_WORKERS: int = 4
"""Finnhub requests in flight at once; the pacer keeps them within the minute's limit."""

FINNHUB_RETRIES: int = 3
"""Times a stock is retried after Finnhub says the minute's limit was reached."""

HISTORY_GIVE_UP_AFTER: int = 25
"""Price histories that may fail in a row before the pass stops, taking the user's
price-history keys to have reached their plans' limits for the night."""

PERCENT, MILLIONS, PLAIN = 0.01, 1e6, 1.0
FIELDS: dict[str, tuple[str, float]] = {
    "market_cap": ("marketCapitalization", MILLIONS),
    "pe_ratio": ("peTTM", PLAIN),
    "price_to_sales": ("psTTM", PLAIN),
    "ev_to_ebitda": ("evEbitdaTTM", PLAIN),
    "gross_margin": ("grossMarginTTM", PERCENT),
    "operating_margin": ("operatingMarginTTM", PERCENT),
    "net_margin": ("netProfitMarginTTM", PERCENT),
    "return_on_equity": ("roeTTM", PERCENT),
    "revenue_growth": ("revenueGrowthTTMYoy", PERCENT),
    "earnings_growth": ("epsGrowthTTMYoy", PERCENT),
    "return_6m": ("26WeekPriceReturnDaily", PERCENT),
    "return_52w": ("52WeekPriceReturnDaily", PERCENT),
    "volatility_3m": ("3MonthADReturnStd", PERCENT),
    "beta": ("beta", PLAIN),
    "average_volume": ("3MonthAverageTradingVolume", MILLIONS),
}
"""The Finnhub basic financials field each :class:`ProviderScreenMetrics` field comes
from, and the factor converting it: Finnhub gives ratios in percent and totals in
millions."""


def _number(value: object) -> float | None:
    """``value`` as a finite float, or ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def parse_screen_metrics(payload: Any) -> ProviderScreenMetrics | None:
    """Read Finnhub's basic financials; ``None`` when they hold no market cap."""
    values = payload.get("metric") if isinstance(payload, dict) else None
    if not isinstance(values, dict):
        return None
    fields: dict[str, float] = {}
    for name, (source, factor) in FIELDS.items():
        number = _number(values.get(source))
        if number is not None:
            fields[name] = number * factor
    price_to_fcf = _number(values.get("pfcfShareTTM"))
    if price_to_fcf is not None and price_to_fcf > 0:
        fields["fcf_yield"] = 1 / price_to_fcf
    if fields.get("market_cap", 0.0) <= 0:
        return None
    return ProviderScreenMetrics(**fields)


T = TypeVar("T")


async def _paced_finnhub(
    symbols: Iterable[str],
    path: str,
    params: dict[str, str],
    parse: Callable[[Any], T | None],
) -> dict[str, T]:
    """Ask Finnhub's ``path`` about each of ``symbols`` on the user's key, paced at
    :data:`FINNHUB_NIGHTLY_PER_MINUTE`, keeping what ``parse`` reads from each answer.
    Stocks Finnhub does not cover, or fails on, are left out.

    Raises:
        ProviderNotConfiguredError: If the user has no Finnhub key, or it is rejected.
    """
    api_key = finnhub_key()
    if api_key is None:
        raise ProviderNotConfiguredError("The nightly refresh needs your Finnhub key.")
    queue: asyncio.Queue[str] = asyncio.Queue()
    for symbol in symbols:
        queue.put_nowait(symbol)
    pacer = RequestPacer(FINNHUB_NIGHTLY_PER_MINUTE)
    found: dict[str, T] = {}

    async def one(symbol: str, client: httpx2.AsyncClient) -> None:
        query = {"symbol": finnhub_symbol(symbol)} | params
        for attempt in range(FINNHUB_RETRIES + 1):
            await pacer.wait()
            try:
                payload = await finnhub_get(path, query, api_key, client)
            except FinnhubRateLimitError as exc:
                if attempt == FINNHUB_RETRIES:
                    logger.info("Finnhub stayed at its limit; %s is left out.", symbol)
                    return
                await asyncio.sleep(exc.retry_after or 60)
                continue
            except ProviderNotConfiguredError:
                raise
            except DataFetchError as exc:
                logger.debug("No Finnhub %s for %s: %s", path, symbol, exc)
                return
            parsed = parse(payload)
            if parsed is not None:
                found[symbol] = parsed
            return

    async def worker(client: httpx2.AsyncClient) -> None:
        while not queue.empty():
            await one(queue.get_nowait(), client)

    async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        await asyncio.gather(*(worker(client) for _ in range(FINNHUB_WORKERS)))
    return found


async def fetch_screen_metrics(symbols: Iterable[str]) -> dict[str, ProviderScreenMetrics]:
    """Finnhub's metrics for each of ``symbols`` it has them for, on the user's key; about
    80 minutes for 4,000 stocks.

    Raises:
        ProviderNotConfiguredError: If the user has no Finnhub key, or it is rejected.
    """
    return await _paced_finnhub(symbols, "stock/metric", {"metric": "all"}, parse_screen_metrics)


def parse_quote_price(payload: Any) -> float | None:
    """The current price in a Finnhub quote; ``None`` for its all-zero answer."""
    price = _number(payload.get("c")) if isinstance(payload, dict) else None
    return price if price is not None and price > 0 else None


async def fetch_quote_prices(symbols: Iterable[str]) -> dict[str, float]:
    """The current price of each of ``symbols``, from Finnhub quotes on the user's key.

    Raises:
        ProviderNotConfiguredError: If the user has no Finnhub key, or it is rejected.
    """
    return await _paced_finnhub(symbols, "quote", {}, parse_quote_price)


async def fetch_histories(symbols: Iterable[str], start: date) -> dict[str, pd.DataFrame]:
    """Daily ``Close`` and ``Volume`` since ``start`` for as many of ``symbols`` as the
    user's price-history keys allow tonight, in the order given.

    Without any price-history key, nothing is fetched. After
    :data:`HISTORY_GIVE_UP_AFTER` failures in a row, the keys are taken to have reached
    their limits and the rest are left out.
    """
    histories: dict[str, pd.DataFrame] = {}
    failures = 0
    cutoff = pd.Timestamp(start)
    for symbol in symbols:
        try:
            frame = await fetch_price_history(symbol, "2y", "1d")
        except ProviderNotConfiguredError:
            logger.info("No price-history key, so price-history columns are left empty.")
            return histories
        except DataFetchError as exc:
            failures += 1
            logger.debug("No price history for %s: %s", symbol, exc)
            if failures >= HISTORY_GIVE_UP_AFTER:
                logger.info(
                    "Price histories failed %d times in a row, so the keys' limits are taken "
                    "to be reached; %d stocks have price-history columns.",
                    failures,
                    len(histories),
                )
                return histories
            continue
        failures = 0
        index = pd.DatetimeIndex(frame.index)
        if index.tz is not None:
            index = index.tz_localize(None)
        window = frame.set_axis(index)[["Close", "Volume"]]
        window = window[window.index >= cutoff]
        if not window.empty:
            histories[symbol] = window
    return histories
