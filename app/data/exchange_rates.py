"""Currency exchange rates, for valuing foreign issuers whose statements are not in dollars.

Rates are spot quotes of currency pairs from Financial Modeling Prep, on the FMP key of
the user a request is served for, or the screener's own key while it refreshes (see
:mod:`app.core.credentials`). FMP quotes some pairs only one way round (``USDTWD`` but
not ``TWDUSD``), so the inverse pair is tried too. When FMP fails or the user has no FMP
key, Alpha Vantage's exchange rate on the user's Alpha Vantage key is next. Like other
market data, rates come from yfinance only when ``YFINANCE_FALLBACK`` is on, for
development.

A rate is reused for :data:`FRESH_SECONDS`, kept in process memory and, when
``REDIS_URL`` is set, in Redis, per provider, so rates FMP returned are only served to
users who query FMP themselves.
"""

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import yfinance as yf

from app.core.cache import cache_get, cache_set
from app.data.fetcher import (
    DataFetchError,
    Provider,
    ProviderPlanError,
    SymbolNotFoundError,
    alpha_vantage_get,
    alpha_vantage_number,
    available_providers,
    first_success,
    fmp_get,
)

logger = logging.getLogger(__name__)

FRESH_SECONDS: float = 60 * 60
"""How long a rate is reused; valuation multiples are not sensitive to intraday moves."""

_memory: dict[tuple[Provider, str, str], tuple[float, float]] = {}
"""(provider, base, quote) -> (time fetched, rate)."""


def clear_memory() -> None:
    """Forget the rates kept in memory."""
    _memory.clear()


def _positive(value: object) -> float | None:
    """``value`` as a positive, finite float, or ``None``."""
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


async def _fmp_rate(base: str, quote: str, api_key: str) -> float:
    """The latest ``quote`` per ``base`` rate from FMP, inverting the reverse pair if needed.

    Raises:
        ProviderPlanError: If the plan covers neither pair.
        SymbolNotFoundError: If FMP quotes neither pair.
    """
    plan_error: ProviderPlanError | None = None
    for pair, inverted in ((f"{base}{quote}", False), (f"{quote}{base}", True)):
        try:
            payload: Any = await fmp_get("quote", {"symbol": pair}, api_key, None)
        except ProviderPlanError as exc:
            plan_error = exc
            continue
        price = (
            _positive(payload[0].get("price")) if isinstance(payload, list) and payload else None
        )
        if price is not None:
            return 1.0 / price if inverted else price
    if plan_error is not None:
        raise plan_error
    raise SymbolNotFoundError(f"FMP has no {base}/{quote} exchange rate.")


async def _alpha_vantage_rate(base: str, quote: str, api_key: str) -> float:
    """The latest ``quote`` per ``base`` rate from Alpha Vantage."""
    payload = await alpha_vantage_get(
        "CURRENCY_EXCHANGE_RATE", {"from_currency": base, "to_currency": quote}, api_key
    )
    answer: Any = payload.get("Realtime Currency Exchange Rate") or {}
    rate = _positive(alpha_vantage_number(answer.get("5. Exchange Rate")))
    if rate is None:
        raise SymbolNotFoundError(f"Alpha Vantage has no {base}/{quote} exchange rate.")
    return rate


def _yfinance_rate(base: str, quote: str) -> float:
    """The latest ``quote`` per ``base`` rate from yfinance (blocking)."""
    try:
        price = yf.Ticker(f"{base}{quote}=X").fast_info.last_price
    except Exception as exc:  # yfinance raises many unrelated exception types.
        raise DataFetchError(f"The {base}/{quote} exchange rate could not be fetched: {exc}")
    rate = _positive(price)
    if rate is None:
        raise SymbolNotFoundError(f"yfinance has no {base}/{quote} exchange rate.")
    return rate


async def fetch_exchange_rate(base: str, quote: str = "USD") -> float:
    """Units of ``quote`` currency per unit of ``base`` currency, e.g. USD per TWD.

    Args:
        base: ISO 4217 code of the currency converted from.
        quote: ISO 4217 code of the currency converted to.

    Raises:
        ProviderNotConfiguredError: If there is no market data key and the fallback is off.
        DataFetchError: If no rate is available.
    """
    base, quote = base.upper(), quote.upper()
    if base == quote:
        return 1.0

    def attempt(provider: Provider, api_key: str | None) -> Callable[[], Awaitable[float]]:
        async def run() -> float:
            entry = _memory.get((provider, base, quote))
            if entry is not None and time.monotonic() - entry[0] <= FRESH_SECONDS:
                return entry[1]
            key = f"fx:{provider}:{base}{quote}"
            cached = await cache_get(key)
            rate: float | None = None
            if cached is not None:
                try:
                    rate = float(cached)
                except ValueError:
                    logger.warning("Ignoring an unreadable cached %s/%s rate.", base, quote)
            if rate is None:
                if provider == "fmp":
                    assert api_key is not None
                    rate = await _fmp_rate(base, quote, api_key)
                elif provider == "alpha_vantage":
                    assert api_key is not None
                    rate = await _alpha_vantage_rate(base, quote, api_key)
                else:
                    rate = await asyncio.to_thread(_yfinance_rate, base, quote)
                await cache_set(key, repr(rate), FRESH_SECONDS)
            _memory[provider, base, quote] = (time.monotonic(), rate)
            return rate

        return run

    return await first_success(
        f"{base}/{quote}",
        "an exchange rate",
        [(provider, attempt(provider, key)) for provider, key in available_providers()],
    )


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
