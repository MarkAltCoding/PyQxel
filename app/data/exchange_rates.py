"""Currency exchange rates, for valuing foreign issuers whose statements are not in dollars.

Rates are spot rates from Yahoo Finance's currency pairs (``TWDUSD=X``). They come from
yfinance whatever ``YFINANCE_FALLBACK`` says: they are public reference data, not an
account's market data, and FMP plans below the paid tiers quote only a few pairs.

A rate is reused for :data:`FRESH_SECONDS`, kept in process memory and, when
``REDIS_URL`` is set, in Redis.
"""

import asyncio
import logging
import math
import time
from collections.abc import Iterable

import yfinance as yf

from app.core.cache import cache_get, cache_set
from app.data.fetcher import DataFetchError

logger = logging.getLogger(__name__)

FRESH_SECONDS: float = 60 * 60
"""How long a rate is reused; valuation multiples are not sensitive to intraday moves."""

_memory: dict[tuple[str, str], tuple[float, float]] = {}
"""(base, quote) -> (time fetched, rate)."""


def clear_memory() -> None:
    """Forget the rates kept in memory."""
    _memory.clear()


def _yfinance_rate(base: str, quote: str) -> float:
    """The latest ``quote`` per ``base`` rate from yfinance (blocking)."""
    try:
        price = yf.Ticker(f"{base}{quote}=X").fast_info.last_price
    except Exception as exc:  # yfinance raises many unrelated exception types.
        raise DataFetchError(f"The {base}/{quote} exchange rate could not be fetched: {exc}")
    if price is None or not math.isfinite(price) or price <= 0:
        raise DataFetchError(f"No {base}/{quote} exchange rate is available.")
    return float(price)


async def fetch_exchange_rate(base: str, quote: str = "USD") -> float:
    """Units of ``quote`` currency per unit of ``base`` currency, e.g. USD per TWD.

    Args:
        base: ISO 4217 code of the currency converted from.
        quote: ISO 4217 code of the currency converted to.

    Raises:
        DataFetchError: If no rate is available.
    """
    base, quote = base.upper(), quote.upper()
    if base == quote:
        return 1.0
    entry = _memory.get((base, quote))
    if entry is not None and time.monotonic() - entry[0] <= FRESH_SECONDS:
        return entry[1]
    key = f"fx:{base}{quote}"
    cached = await cache_get(key)
    rate: float | None = None
    if cached is not None:
        try:
            rate = float(cached)
        except ValueError:
            logger.warning("Ignoring an unreadable cached %s/%s rate.", base, quote)
    if rate is None:
        rate = await asyncio.to_thread(_yfinance_rate, base, quote)
        await cache_set(key, repr(rate), FRESH_SECONDS)
    _memory[base, quote] = (time.monotonic(), rate)
    return rate


async def fetch_exchange_rates(currencies: Iterable[str], quote: str = "USD") -> dict[str, float]:
    """Units of ``quote`` per unit of each of ``currencies`` that has a rate.

    Currencies without one are left out and logged, so one missing rate does not stop
    the others.
    """
    rates: dict[str, float] = {}
    for currency in sorted(set(currencies)):
        try:
            rates[currency] = await fetch_exchange_rate(currency, quote)
        except DataFetchError as exc:
            logger.warning("%s", exc)
    return rates
