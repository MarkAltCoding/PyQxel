"""Currency exchange rates, for valuing foreign issuers whose statements are not in dollars.

The European Central Bank's euro reference rates come first. They are published each
working day for the euro and 29 other currencies, may be reused freely with the ECB
credited as the source, and need no key, so every account is served them. A pair of two
such currencies is their cross rate through the euro.

A currency the ECB does not publish, such as the Taiwan dollar, is quoted by the
providers whose keys the request carries: Financial Modeling Prep, on the user's FMP key
or the screener's own while it refreshes (see :mod:`app.core.credentials`), then Alpha
Vantage on the user's key. FMP quotes some pairs only one way round (``USDTWD`` but not
``TWDUSD``), so the inverse pair is tried too. Like other market data, they come from
yfinance only for a request allowed to use it.

Every rate carries its source, which is named wherever the rate is shown. A provider's
rate is reused for :data:`FRESH_SECONDS`, kept in process memory and, when ``REDIS_URL``
is set, in Redis, per provider, so rates FMP returned are only served to users who query
FMP themselves.
"""

import asyncio
import json
import logging
import math
import time
import xml.etree.ElementTree as ElementTree
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx2
import yfinance as yf

from app.core.cache import cache_get, cache_set
from app.data.fetcher import (
    HTTP_TIMEOUT_SECONDS,
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

ECB_URL: str = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
"""The ECB's euro reference rates of the latest working day."""

ECB_FRESH_SECONDS: float = 6 * 60 * 60
"""How long the ECB's rates are reused; they change once a working day, around 16:00 CET."""

ECB_CACHE_KEY: str = "fx:ecb"

ECB_MAX_BYTES: int = 64 * 1024
"""Largest response read as the ECB's rates; the real one is about 1.5 kB."""

PROVIDER_SOURCES: dict[Provider, str] = {
    "fmp": "Financial Modeling Prep's latest quote",
    "alpha_vantage": "Alpha Vantage's latest rate",
    "finnhub": "Finnhub's latest quote",
    "yfinance": "yfinance's latest quote",
}
"""How a provider's rate is credited where it is shown."""


@dataclass(frozen=True)
class ExchangeRate:
    """Units of the quote currency per unit of the base currency, and where it came from."""

    rate: float
    source: str
    """The rate's source, as it is named where the rate is shown, e.g. "the European Central
    Bank's reference rate for Oct 30, 2026"."""


@dataclass(frozen=True)
class EcbRates:
    """The ECB's reference rates of one working day: units of each currency per euro."""

    day: date
    per_euro: dict[str, float]
    """Including the euro itself, at 1."""

    def cross(self, base: str, quote: str) -> ExchangeRate | None:
        """Units of ``quote`` per ``base`` through the euro; ``None`` unless the ECB publishes
        both.

        The ECB publishes rates per euro, so any other pair is calculated from two of them,
        and its source says so, as the ECB's terms ask of modified figures.
        """
        if base not in self.per_euro or quote not in self.per_euro:
            return None
        day = f"{self.day.strftime('%b')} {self.day.day}, {self.day.year}"
        published = base == "EUR"
        return ExchangeRate(
            rate=self.per_euro[quote] / self.per_euro[base],
            source=(
                f"the European Central Bank's reference rate for {day}"
                if published
                else f"a rate worked out from the European Central Bank's euro reference "
                f"rates for {day}"
            ),
        )


_memory: dict[tuple[Provider, str, str], tuple[float, float]] = {}
"""(provider, base, quote) -> (time fetched, rate)."""

_ecb_memory: tuple[float, EcbRates] | None = None
"""(time fetched, rates)."""


def clear_memory() -> None:
    """Forget the rates kept in memory."""
    global _ecb_memory
    _memory.clear()
    _ecb_memory = None


def parse_ecb_rates(content: bytes) -> EcbRates:
    """The rates in the ECB's daily reference-rate XML.

    Raises:
        DataFetchError: If ``content`` is not that document, or has no rates.
    """
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise DataFetchError(f"The ECB's exchange rates could not be read: {exc}") from exc
    day: date | None = None
    per_euro = {"EUR": 1.0}
    for element in root.iter():
        if not element.tag.endswith("Cube"):
            continue
        if "time" in element.attrib:
            try:
                day = date.fromisoformat(element.attrib["time"])
            except ValueError:
                continue
        currency, value = element.attrib.get("currency"), element.attrib.get("rate")
        if currency and value:
            try:
                rate = _positive(float(value))
            except ValueError:
                rate = None
            if rate is not None and len(currency) == 3 and currency.isalpha():
                per_euro[currency.upper()] = rate
    if day is None or len(per_euro) < 2:
        raise DataFetchError("The ECB's exchange rates could not be read: no rates found.")
    return EcbRates(day=day, per_euro=per_euro)


def _ecb_from_json(text: str) -> EcbRates:
    data = json.loads(text)
    return EcbRates(day=date.fromisoformat(data["day"]), per_euro=dict(data["per_euro"]))


def _ecb_to_json(rates: EcbRates) -> str:
    return json.dumps({"day": rates.day.isoformat(), "per_euro": rates.per_euro})


async def fetch_ecb_rates() -> EcbRates:
    """The ECB's latest reference rates, reused for :data:`ECB_FRESH_SECONDS` from memory
    or Redis. They are public data, so one copy serves every account.

    Raises:
        DataFetchError: If they cannot be fetched or read.
    """
    global _ecb_memory
    if _ecb_memory is not None and time.monotonic() - _ecb_memory[0] <= ECB_FRESH_SECONDS:
        return _ecb_memory[1]
    cached = await cache_get(ECB_CACHE_KEY)
    if cached is not None:
        try:
            rates = _ecb_from_json(cached)
        except ValueError, KeyError, TypeError:
            logger.warning("Ignoring unreadable cached ECB rates.")
        else:
            _ecb_memory = (time.monotonic(), rates)
            return rates
    try:
        async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(ECB_URL)
            response.raise_for_status()
    except httpx2.HTTPError as exc:
        raise DataFetchError(f"The ECB's exchange rates could not be fetched: {exc}") from exc
    rates = parse_ecb_rates(response.content[:ECB_MAX_BYTES])
    _ecb_memory = (time.monotonic(), rates)
    await cache_set(ECB_CACHE_KEY, _ecb_to_json(rates), ECB_FRESH_SECONDS)
    return rates


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
    """Units of ``quote`` currency per unit of ``base`` currency, e.g. USD per TWD; see
    :func:`fetch_sourced_exchange_rate`, which also says where the rate came from."""
    return (await fetch_sourced_exchange_rate(base, quote)).rate


async def fetch_sourced_exchange_rate(base: str, quote: str = "USD") -> ExchangeRate:
    """Units of ``quote`` currency per unit of ``base`` currency, and its source.

    The ECB's reference rates answer when it publishes both currencies; the request's
    providers answer otherwise, or when the ECB's rates cannot be fetched.

    Args:
        base: ISO 4217 code of the currency converted from.
        quote: ISO 4217 code of the currency converted to.

    Raises:
        ProviderNotConfiguredError: If the ECB does not publish the pair and there is no
            market data key.
        DataFetchError: If no rate is available.
    """
    base, quote = base.upper(), quote.upper()
    if base == quote:
        return ExchangeRate(rate=1.0, source="")
    try:
        found = (await fetch_ecb_rates()).cross(base, quote)
    except DataFetchError as exc:
        logger.warning("%s; asking the market data providers instead.", exc)
        found = None
    if found is not None:
        return found

    def attempt(provider: Provider, api_key: str | None) -> Callable[[], Awaitable[ExchangeRate]]:
        async def run() -> ExchangeRate:
            source = PROVIDER_SOURCES.get(provider, "the latest rate")
            entry = _memory.get((provider, base, quote))
            if entry is not None and time.monotonic() - entry[0] <= FRESH_SECONDS:
                return ExchangeRate(rate=entry[1], source=source)
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
            return ExchangeRate(rate=rate, source=source)

        return run

    return await first_success(
        f"{base}/{quote}",
        "an exchange rate",
        [(provider, attempt(provider, key)) for provider, key in available_providers("fx")],
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


MINOR_UNITS: dict[str, tuple[str, float]] = {
    "GBX": ("GBP", 100.0),
    "GBp": ("GBP", 100.0),
    "ZAC": ("ZAR", 100.0),
    "ZAc": ("ZAR", 100.0),
    "ILA": ("ILS", 100.0),
    "ILa": ("ILS", 100.0),
}
"""Currencies some exchanges quote prices in that are a hundredth of another: London's
pence (GBX), Johannesburg's cents (ZAC) and Tel Aviv's agorot (ILA). Case matters: GBp is
pence, GBP pounds."""


async def dollars_per_unit(currency: str | None) -> float | None:
    """US dollars per unit of ``currency``, a price's currency, at the latest rate: 1 for
    dollars or an unknown currency, and a hundredth of the pound's rate for pence.
    ``None`` when no rate can be found."""
    if currency is None or currency.upper() == "USD":
        return 1.0
    major, divisor = MINOR_UNITS.get(currency, (currency.upper(), 1.0))
    try:
        return await fetch_exchange_rate(major, "USD") / divisor
    except DataFetchError as exc:
        logger.warning("No %s rate to convert a price to dollars: %s", major, exc)
        return None
