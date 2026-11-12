"""Async market data fetchers.

Financial Modeling Prep (FMP), a licensed provider, is the primary source, called with
the requesting user's own API key (see :mod:`app.core.credentials`), so each user's
data is billed to their own plan. Alpha Vantage, also licensed and also on the user's
own key, comes next: it answers when FMP fails or the user's FMP plan does not cover a
request, and fills in ticker info fields FMP leaves empty. Finnhub, on the user's own key,
serves quotes and company profiles, which its free plan covers for US listings at 60
requests a minute: it is the first source for US quotes, since they are real time and
FMP's free plan quotes only a sample of symbols, and it backs up and fills in profiles.
It has no price history on its free plan. yfinance, which scrapes Yahoo, is used only
when ``YFINANCE_FALLBACK`` is on, for development, or for the admin account when they
turn it on for themselves, after the licensed providers. A user with no usable key gets
:class:`ProviderNotConfiguredError`, and a request their plan excludes,
:class:`ProviderPlanError`.

Results are cached per provider, in Redis when ``REDIS_URL`` is set and for a few
seconds in memory for quotes; Alpha Vantage's answers are also kept in memory, since
its free plan allows only 25 requests a day. Data a licensed provider returned is only
served from the cache to users who query that provider themselves, so no one reads
licensed data on another user's plan.

Providers answer an unknown symbol with an empty result rather than an error, which is
reported as :class:`SymbolNotFoundError`. Transport and rate-limit failures are reported
as the broader :class:`DataFetchError`.
"""

import asyncio
import hashlib
import json
import logging
import math
import time
import zlib
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, TypeVar
from zoneinfo import ZoneInfo

import httpx2
import numpy as np
import pandas as pd
import yfinance as yf
from sqlalchemy.exc import SQLAlchemyError

from app.core.text import listed
from app.core.cache import cache_get, cache_increment, cache_set
from app.core.credentials import current_provider_keys
from app.core.redact import redact
from app.data import usage
from app.db import price_store
from app.db.price_store import StoredHistory
from app.db.session import get_sessionmaker
from app.models.quote import Quote
from app.models.stock import TickerInfo

logger = logging.getLogger(__name__)

FMP_BASE_URL: str = "https://financialmodelingprep.com/stable"
ALPHA_VANTAGE_URL: str = "https://www.alphavantage.co/query"
FINNHUB_URL: str = "https://finnhub.io/api/v1"
TIINGO_URL: str = "https://api.tiingo.com"
EODHD_URL: str = "https://eodhd.com/api"
HTTP_TIMEOUT_SECONDS: float = 10.0
OHLCV_COLUMNS: list[str] = ["Open", "High", "Low", "Close", "Volume"]
QUOTE_TTL_SECONDS: float = 5.0
"""How long a fetched quote is reused before the provider is asked again."""

INFO_TTL_SECONDS: float = 300.0
"""How long ticker info stays in the shared cache."""
HISTORY_TTL_SECONDS: float = 300.0
"""How long daily and longer bars stay in the shared cache; the last bar moves intraday."""
INTRADAY_HISTORY_TTL_SECONDS: float = 60.0
"""How long minute and hourly bars stay in the shared cache."""

ALPHA_VANTAGE_TTL_SECONDS: dict[str, float] = {
    "OVERVIEW": 24 * 60 * 60,
    "INCOME_STATEMENT": 24 * 60 * 60,
    "BALANCE_SHEET": 24 * 60 * 60,
    "CASH_FLOW": 24 * 60 * 60,
    "TIME_SERIES_DAILY_ADJUSTED": 60 * 60,
    "TIME_SERIES_INTRADAY": 60,
    "GLOBAL_QUOTE": 60,
    "CURRENCY_EXCHANGE_RATE": 60 * 60,
    "ETF_PROFILE": 24 * 60 * 60,
}
"""How long each Alpha Vantage answer is reused, unknown symbols included. Its free plan
allows 25 requests a day, so even quotes are kept a minute."""
ALPHA_VANTAGE_MEMORY_SIZE: int = 512
"""Alpha Vantage answers kept in process memory, for deployments without Redis."""
ALPHA_VANTAGE_PLAN_TTL_SECONDS: float = 24 * 60 * 60
"""How long a key's plan is taken not to cover a premium function before asking again."""
ALPHA_VANTAGE_FREE_FUNCTIONS: frozenset[str] = frozenset(
    {"GLOBAL_QUOTE", "SYMBOL_SEARCH", "TIME_SERIES_DAILY", "INCOME_STATEMENT",
     "BALANCE_SHEET", "CASH_FLOW", "CURRENCY_EXCHANGE_RATE", "ETF_PROFILE"}
)  # fmt: skip
"""Functions a free key may call, as checked against a free key in October 2026 (and
``ETF_PROFILE`` on November 5, 2026); the company overview, adjusted daily bars, and every
intraday series need a paid plan.
Alpha Vantage answers an invalid key as if it asked for a premium function, so a premium
refusal of one of these means a bad key."""
ALPHA_VANTAGE_SPACING_SECONDS: float = 1.1
"""Least time between Alpha Vantage requests from this process: free keys refuse bursts
of more than one request a second."""
ALPHA_VANTAGE_FREE_DAILY_BARS: int = 100
"""Daily bars Alpha Vantage's free plan serves, the latest ones, unadjusted."""
FMP_REFUSAL_TTL_SECONDS: float = 24 * 60 * 60
"""How long a key's FMP plan is taken not to cover a request before asking again. A free
plan serves prices and quotes for a short sample list of symbols and refuses the rest,
and refuses intraday bars outright; remembering this saves its 250 requests a day for
the symbols it does serve."""
FMP_LIMIT_PAUSE_SECONDS: float = 30 * 60
"""How long FMP is left out for a key after it answers that the key's limit is reached,
such as a free plan's 250 requests a day."""
ALPHA_VANTAGE_LIMIT_PAUSE_SECONDS: float = 60 * 60
"""How long Alpha Vantage is left out for a key after its daily limit is reached."""
FINNHUB_REQUESTS_PER_MINUTE: int = 55
"""Finnhub requests sent per key per minute until Finnhub has reported the key's own limit,
under the free plan's 60. Beyond it, quotes fall to the next provider rather than
spending the key's allowance meant for news."""
FINNHUB_PACE_SHARE: float = 0.92
"""The share of a key's per-minute limit, as Finnhub reports it, that PyQxel sends: 55 of
the free plan's 60, or 828 of a paid plan's 900. The rest is room for clocks that differ
and for requests the user sends from elsewhere."""
FINNHUB_FREE_PER_MINUTE: int = 60
"""The requests a minute Finnhub's free plan allows."""
FINNHUB_MOST_PER_MINUTE: int = 30 * 60
"""Finnhub's cap of 30 requests a second, which holds on every plan above its own limit."""
FINNHUB_REFUSAL_TTL_SECONDS: float = 24 * 60 * 60
"""How long a key's Finnhub plan is taken not to cover a request for a symbol."""
FINNHUB_LIMIT_PAUSE_SECONDS: float = 60
"""How long Finnhub is left out for a key after it answers that the rate limit is reached."""
FINNHUB_PROFILE_TTL_SECONDS: float = 24 * 60 * 60
"""How long a Finnhub company profile, used to fill in ticker info, is reused."""
TIINGO_REFUSAL_TTL_SECONDS: float = 24 * 60 * 60
"""How long a Tiingo key's plan is taken not to cover a request before asking again."""
TIINGO_LIMIT_PAUSE_SECONDS: float = 60 * 60
"""How long Tiingo is left out once a key's limit is reached: its free plan allows 50
requests an hour, 1,000 a day and 500 different symbols a month."""
TIINGO_META_TTL_SECONDS: float = 24 * 60 * 60
"""How long Tiingo's company meta, asked for to fill in a holding's name and sector, is
reused: every portfolio load would otherwise spend a Tiingo request on each holding."""
EODHD_REFUSAL_TTL_SECONDS: float = 24 * 60 * 60
"""How long an EODHD key's plan is taken not to cover a request before asking again."""
EODHD_LIMIT_PAUSE_SECONDS: float = 6 * 60 * 60
"""How long EODHD is left out once a key's daily limit is reached: its free plan allows 20
requests a day."""
EODHD_FREE_HISTORY_DAYS: int = 366
"""Days of daily bars EODHD's free plan serves, back from today."""
RECALL_MEMORY_SIZE: int = 4096
"""Refusals and pauses kept in process memory, in front of Redis."""
HISTORY_NOTICE: str = "notice"
"""Key in a history frame's ``attrs`` for a provider's note on what the bars lack."""

Provider = Literal["tiingo", "fmp", "eodhd", "finnhub", "alpha_vantage", "yfinance"]

DataKind = Literal["history", "intraday", "quote", "info", "fx"]
"""What is asked of the providers: daily (or longer) bars, intraday bars, a quote, a
company profile, or an exchange rate."""

SymbolKind = Literal["us", "index", "international"]
"""A US listing (stocks, ETFs, and funds), an index such as ^GSPC, or a listing abroad,
which carries an exchange suffix such as .L."""

PROVIDER_ORDER: dict[tuple[DataKind, SymbolKind], tuple[Provider, ...]] = {
    ("history", "us"): ("tiingo", "fmp", "eodhd", "alpha_vantage", "finnhub"),
    ("history", "index"): ("eodhd", "fmp"),
    ("history", "international"): ("eodhd", "alpha_vantage", "fmp", "finnhub"),
    ("intraday", "us"): ("tiingo", "eodhd", "fmp", "alpha_vantage", "finnhub"),
    ("intraday", "index"): ("eodhd", "fmp"),
    ("intraday", "international"): ("eodhd", "fmp", "alpha_vantage"),
    ("quote", "us"): ("finnhub", "tiingo", "fmp", "eodhd", "alpha_vantage"),
    ("quote", "index"): ("fmp", "eodhd"),
    ("quote", "international"): ("eodhd", "fmp", "alpha_vantage", "finnhub"),
    ("info", "us"): ("fmp", "finnhub", "tiingo", "alpha_vantage", "eodhd"),
    ("info", "index"): ("fmp", "eodhd"),
    ("info", "international"): ("fmp", "eodhd", "alpha_vantage", "finnhub"),
    ("fx", "us"): ("fmp", "alpha_vantage"),
}
"""The providers that can answer each kind of data for each kind of symbol, cheapest
first, as ``docs/data-sources.md`` sets out: a provider whose free plan covers the request
comes before one that needs a paid plan for it. Each is tried only with the user's own
key, and one whose plan refuses passes the request on to the next. yfinance follows them
all for a request allowed to use it."""

PROVIDER_NAMES: dict[str, str] = {
    "tiingo": "Tiingo",
    "fmp": "Financial Modeling Prep",
    "eodhd": "EODHD",
    "finnhub": "Finnhub",
    "alpha_vantage": "Alpha Vantage",
    "yfinance": "yfinance",
}

KIND_NAMES: dict[DataKind, str] = {
    "history": "Price history",
    "intraday": "Intraday prices",
    "quote": "Live quotes",
    "info": "Company profiles",
    "fx": "Exchange rates",
}

DEFAULT_TIMEZONE: str = "America/New_York"
SUFFIX_TIMEZONES: dict[str, str] = {
    ".L": "Europe/London",
    ".DE": "Europe/Berlin",
    ".F": "Europe/Berlin",
    ".PA": "Europe/Paris",
    ".AS": "Europe/Amsterdam",
    ".SW": "Europe/Zurich",
    ".MC": "Europe/Madrid",
    ".MI": "Europe/Rome",
    ".ST": "Europe/Stockholm",
    ".TO": "America/Toronto",
    ".V": "America/Toronto",
    ".MX": "America/Mexico_City",
    ".SA": "America/Sao_Paulo",
    ".T": "Asia/Tokyo",
    ".HK": "Asia/Hong_Kong",
    ".SS": "Asia/Shanghai",
    ".SZ": "Asia/Shanghai",
    ".KS": "Asia/Seoul",
    ".NS": "Asia/Kolkata",
    ".BO": "Asia/Kolkata",
    ".AX": "Australia/Sydney",
    ".BR": "Europe/Brussels",
    ".LS": "Europe/Lisbon",
    ".OL": "Europe/Oslo",
    ".CO": "Europe/Copenhagen",
    ".HE": "Europe/Helsinki",
    ".IR": "Europe/Dublin",
    ".VI": "Europe/Vienna",
    ".WA": "Europe/Warsaw",
    ".PR": "Europe/Prague",
    ".AT": "Europe/Athens",
    ".BD": "Europe/Budapest",
    ".IS": "Europe/Istanbul",
    ".NE": "America/Toronto",
    ".BA": "America/Argentina/Buenos_Aires",
    ".SN": "America/Santiago",
    ".KQ": "Asia/Seoul",
    ".TW": "Asia/Taipei",
    ".TWO": "Asia/Taipei",
    ".SI": "Asia/Singapore",
    ".JK": "Asia/Jakarta",
    ".BK": "Asia/Bangkok",
    ".KL": "Asia/Kuala_Lumpur",
    ".NZ": "Pacific/Auckland",
    ".JO": "Africa/Johannesburg",
    ".TA": "Asia/Jerusalem",
    ".SR": "Asia/Riyadh",
}
"""Exchange time zones by Yahoo-style symbol suffix; other symbols trade in New York.
FMP dates its bars by exchange-local date, and they are stamped at midnight there, as
yfinance stamps them."""

INTRADAY_INTERVALS: dict[str, tuple[str, str | None]] = {
    "1m": ("1min", None),
    "2m": ("1min", "2min"),
    "5m": ("5min", None),
    "15m": ("15min", None),
    "30m": ("30min", None),
    "60m": ("1hour", None),
    "1h": ("1hour", None),
    "90m": ("30min", "90min"),
}
"""FMP chart interval for each intraday bar size, and the size to resample to, if any."""

LONG_INTERVALS: dict[str, str] = {"5d": "5D", "1wk": "W-MON", "1mo": "MS", "3mo": "QS"}
"""Bar sizes longer than a day, built from daily bars with these pandas frequencies."""

_quote_cache: dict[tuple[Provider, str], tuple[float, "Quote"]] = {}
_alpha_vantage_memory: "OrderedDict[str, tuple[float, str]]" = OrderedDict()
"""Cache key -> (time fetched, JSON answer)."""
_alpha_vantage_premium: dict[tuple[str, str], float] = {}
"""(key digest, request) -> when the key's plan was found not to cover the request."""
_alpha_vantage_next_slot: float = 0.0
"""Monotonic time from which the next Alpha Vantage request may be sent."""
_recalled: "OrderedDict[str, tuple[float, float]]" = OrderedDict()
"""Recall key -> (monotonic time it expires, wall-clock time it ends): refusals and pauses
known to this process."""
_tiingo_meta_memory: dict[str, tuple[float, dict[str, Any]]] = {}
"""Cache key -> (time fetched, Tiingo company meta), for deployments without Redis."""
_finnhub_counts: dict[tuple[str, int], int] = {}
"""(key digest, minute) -> Finnhub requests sent, for deployments without Redis."""

T = TypeVar("T")


class DataFetchError(RuntimeError):
    """Raised when market data cannot be retrieved from any provider.

    The message is scrubbed of keys (see :mod:`app.core.redact`), since it often quotes
    a provider's error, and errors quote the URL the user's key was sent in.
    """

    def __init__(self, message: str = "", *args: object) -> None:
        super().__init__(redact(str(message)), *args)


class SymbolNotFoundError(DataFetchError):
    """Raised when a provider answers successfully but has no data for the symbol."""


class ProviderNotConfiguredError(DataFetchError):
    """Raised when the user has no usable key for the data and the yfinance fallback is off."""


class ProviderPlanError(DataFetchError):
    """Raised when the user's plan with a provider does not cover the request.

    ``remembered`` is true when the refusal was recalled rather than just received.
    """

    def __init__(self, message: str, remembered: bool = False) -> None:
        super().__init__(message)
        self.remembered = remembered


class SourceTurnedOffError(DataFetchError):
    """Raised when the data comes only from a source the server does not use, as one whose
    terms do not clearly allow use in a paid product."""


class AlphaVantageBurstError(DataFetchError):
    """Raised when Alpha Vantage refuses requests sent less than a second apart."""


class ProviderLimitError(DataFetchError):
    """Raised when a key has used up its plan's requests for now.

    ``provider`` names the provider, and ``resets_at`` is when PyQxel will ask it again:
    when the plan's limit resets, where the provider says, or else after a pause.
    """

    def __init__(
        self, message: str, provider: "Provider | None" = None, resets_at: datetime | None = None
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.resets_at = resets_at


class AlphaVantageDailyLimitError(ProviderLimitError):
    """Raised when a key has used up its Alpha Vantage requests for the day."""


class FinnhubRateLimitError(ProviderLimitError):
    """Raised when a key has sent its Finnhub requests for the minute."""

    def __init__(
        self,
        message: str,
        retry_after: int | None = None,
        resets_at: datetime | None = None,
    ) -> None:
        if resets_at is None and retry_after is not None:
            resets_at = datetime.now(timezone.utc) + timedelta(seconds=retry_after)
        super().__init__(message, "finnhub", resets_at)
        self.retry_after = retry_after


def _normalize_symbol(symbol: str) -> str:
    """Return ``symbol`` stripped and upper-cased, rejecting empty input."""
    cleaned = symbol.strip().upper()
    if not cleaned:
        raise ValueError("Ticker symbol must not be empty.")
    return cleaned


def finnhub_symbol(symbol: str) -> str:
    """``symbol`` as Finnhub names it: share classes take a dot, as in ``BRK.B``."""
    symbol = symbol.strip().upper()
    return symbol.replace("-", ".") if "." not in symbol else symbol


def finnhub_free_covers(symbol: str) -> bool:
    """Whether Finnhub's free plan serves ``symbol``: US listings, not indices or foreign
    listings, which carry an exchange suffix."""
    symbol = symbol.strip().upper()
    return bool(symbol) and "." not in symbol and not symbol.startswith("^") and "=" not in symbol


def symbol_kind(symbol: str) -> SymbolKind:
    """Whether ``symbol`` is a US listing, an index, or a listing abroad."""
    symbol = symbol.strip().upper()
    if symbol.startswith("^"):
        return "index"
    if "." in symbol or "=" in symbol:
        return "international"
    return "us"


def yfinance_fallback_on() -> bool:
    """Whether yfinance answers the current request: only when its user may see unlicensed
    data and has it on (see :func:`app.api.auth.uses_yfinance`), or for a screener refresh
    built for that user alone. Never from ``YFINANCE_FALLBACK`` by itself, so a request
    that is not marked never gets yfinance data, cached or not."""
    return current_provider_keys().yfinance


def available_providers(
    kind: DataKind = "history", symbol: str = ""
) -> list[tuple[Provider, str | None]]:
    """The providers to try for the current user, in order, with the key to use: those of
    :data:`PROVIDER_ORDER` the user has a key for, then yfinance where it is allowed.

    Raises:
        ProviderNotConfiguredError: If the user has no key that can answer, naming the
            ones that would.
    """
    keys = current_provider_keys()
    every = PROVIDER_ORDER.get((kind, symbol_kind(symbol)), PROVIDER_ORDER.get((kind, "us"), ()))
    order = tuple(provider for provider in every if provider_covers(provider, symbol))
    providers: list[tuple[Provider, str | None]] = []
    for provider in order:
        secret = getattr(keys, provider, None)
        if secret is not None:
            providers.append((provider, secret.get_secret_value()))
    if yfinance_fallback_on():
        providers.append(("yfinance", None))
    if not providers and any(
        getattr(keys, provider, None) is not None for provider in every if provider not in order
    ):
        names = listed([PROVIDER_NAMES[provider] for provider in order], "or")
        raise ProviderPlanError(
            f"None of your keys' providers lists {symbol} on {exchange_name(symbol)}: "
            f"only a paid {names} plan covers it."
        )
    if not providers:
        names = listed([PROVIDER_NAMES[provider] for provider in order], "or")
        raise ProviderNotConfiguredError(
            f"{KIND_NAMES[kind]} come from your own market data keys: add a {names} key "
            "in Settings."
        )
    return providers


def keys_to_add(kind: DataKind, symbol: str = "") -> str:
    """A sentence naming the providers that could answer ``kind`` for ``symbol`` whose keys
    the user has not given, cheapest first; empty when they have given every one."""
    keys = current_provider_keys()
    order = PROVIDER_ORDER.get((kind, symbol_kind(symbol)), ())
    names = [
        PROVIDER_NAMES[p]
        for p in order
        if getattr(keys, p, None) is None and provider_covers(p, symbol)
    ]
    if not names:
        return ""
    return f"A {listed(names, 'or')} key may cover it: add one in Settings."


US_LISTING_TTL_SECONDS: float = 7 * 24 * 60 * 60
"""How long the US listings found for a listing abroad are reused."""
_us_listing_memory: dict[str, tuple[float, list[str]]] = {}
"""Symbol abroad -> (time found, its company's US listings), for deployments without Redis."""


def _name_words(name: str) -> list[str]:
    """A company name's words, lower case, without punctuation or legal endings, for
    telling whether two listings are the same company's."""
    endings = {"inc", "corp", "corporation", "co", "ltd", "plc", "ag", "se", "sa", "nv",
               "ab", "asa", "spa", "kk", "limited", "holding", "holdings", "group", "the",
               "adr"}  # fmt: skip
    words = "".join(c if c.isalnum() else " " for c in name.lower()).split()
    return [word for word in words if word not in endings]


async def us_listings(symbol: str) -> list[str]:
    """US listings of the company behind a listing abroad, such as Toyota's TM for 7203.T,
    which Tiingo's free plan prices: its depositary receipts on a US exchange, or its
    shares traded over the counter. Exchange-listed tickers (four letters or fewer) come
    first. Empty without both a Finnhub key, whose search names the company, and a Tiingo
    key, whose search finds its US listings, or when nothing is found.

    A listing in the US is a different security from the one abroad: priced in dollars,
    sometimes as a fraction or multiple of a share, so it is suggested, never swapped in.
    """
    keys = current_provider_keys()
    if symbol_suffix(symbol) is None or keys.finnhub is None or keys.tiingo is None:
        return []
    cache_key = f"uslisting:{symbol}"
    remembered = _us_listing_memory.get(cache_key)
    if remembered is not None and time.monotonic() - remembered[0] < US_LISTING_TTL_SECONDS:
        return list(remembered[1])
    text = await cache_get(cache_key)
    if text is not None:
        try:
            return [str(ticker) for ticker in json.loads(text)]
        except ValueError:
            logger.warning("Ignoring unreadable cached US listings for %s.", symbol)
    try:
        found = await finnhub_get("search", {"q": symbol}, keys.finnhub.get_secret_value(), None)
        name = next(
            (
                str(row.get("description") or "")
                for row in (found or {}).get("result") or []
                if isinstance(row, dict) and str(row.get("symbol", "")).upper() == symbol
            ),
            "",
        )
        words = _name_words(name)
        if not words:
            return []
        rows = await tiingo_get(
            "tiingo/utilities/search",
            {"query": " ".join(words[:3]), "limit": "20"},
            keys.tiingo.get_secret_value(),
        )
    except DataFetchError as exc:
        logger.info("No US listing could be looked up for %s: %s", symbol, exc)
        return []
    tickers = [
        str(row["ticker"]).upper()
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict)
        and row.get("ticker")
        and row.get("countryCode") == "US"
        and row.get("isActive", True)
        and str(row.get("assetType") or "Stock") == "Stock"
        and _name_words(str(row.get("name") or ""))[: len(words[:2])] == words[:2]
    ]
    tickers = sorted(dict.fromkeys(tickers), key=lambda ticker: (len(ticker) > 4, ticker))[:2]
    _us_listing_memory[cache_key] = (time.monotonic(), tickers)
    await cache_set(cache_key, json.dumps(tickers), US_LISTING_TTL_SECONDS)
    return tickers


async def finnhub_abroad_hint(symbol: str, what: str) -> str | None:
    """Why Finnhub refuses ``what`` (such as "news") for a listing abroad, and the
    company's US listing it covers, if any; ``None`` for a listing in the US, whose
    refusal has some other cause.

    Finnhub's free plan covers US listings only, so for a listing abroad this says so,
    names the paid plan that adds them, and points to a US listing found by
    :func:`us_listings`.
    """
    if symbol_suffix(symbol) is None:
        return None
    text = (
        f"Finnhub's free plan covers {what} for US listings only; its paid plans add "
        "listings abroad."
    )
    listings = await us_listings(symbol)
    if listings:
        text += (
            f" The same company also trades in the US as {listed(listings, 'or')}: look "
            f"that up for its {what}."
        )
    return text


async def plan_hint(kind: DataKind, symbol: str) -> str:
    """What could cover ``kind`` for ``symbol`` when the user's plans do not: the keys they
    have not added, and, for a listing abroad, the company's US listings their Tiingo key
    covers."""
    parts = [hint] if (hint := keys_to_add(kind, symbol)) else []
    listings = await us_listings(symbol)
    if listings:
        names = listed(listings)
        parts.append(
            f"The same company also trades in the US as {names}, priced in dollars, which "
            "your Tiingo key covers: look that up instead."
        )
    return " ".join(parts)


def finnhub_key() -> str | None:
    """The current user's Finnhub key, if they stored one."""
    key = current_provider_keys().finnhub
    return None if key is None else key.get_secret_value()


def alpha_vantage_key() -> str | None:
    """The current user's Alpha Vantage key, if they stored one."""
    key = current_provider_keys().alpha_vantage
    return None if key is None else key.get_secret_value()


async def first_success(
    symbol: str,
    what: str,
    attempts: list[tuple[Provider, Callable[[], Awaitable[T]]]],
    partial: Callable[[T], bool] | None = None,
    passed: list[tuple[Provider, Exception]] | None = None,
    no_better: Callable[[Provider, T], bool] | None = None,
) -> T:
    """Return the first provider's answer, trying the next when one fails.

    With ``partial``, an answer it marks as partial, such as a free plan's short history,
    is kept while the later providers are tried, and returned only if none does better.
    ``no_better`` names the later providers known not to beat the partial answer kept,
    such as a free plan known to serve fewer days, which are then not asked.
    With ``passed``, each provider that failed before the answer is added to it, with its
    error, so a caller can say why a better source was not used.

    Raises:
        SymbolNotFoundError: If every provider answered that it has no such symbol.
        DataFetchError: The only provider's own error, such as
            :class:`ProviderPlanError`, or a general one when several failed.
    """
    errors: list[Exception] = []
    fallback: T | None = None
    for provider, attempt in attempts:
        if fallback is not None and no_better is not None and no_better(provider, fallback):
            continue
        try:
            answer = await attempt()
        except Exception as exc:
            errors.append(exc)
            if passed is not None:
                passed.append((provider, exc))
            logger.warning("%s %s lookup failed for %s: %s", provider, what, symbol, exc)
            continue
        if partial is None or not partial(answer):
            return answer
        if fallback is None:
            fallback = answer
    if fallback is not None:
        return fallback
    if errors and all(isinstance(error, SymbolNotFoundError) for error in errors):
        raise SymbolNotFoundError(f"Unknown ticker symbol {symbol!r}.") from errors[-1]
    if len(errors) == 1 and isinstance(errors[0], DataFetchError):
        raise errors[0]
    refusals = [error for error in errors if not isinstance(error, SymbolNotFoundError)]
    if (
        refusals
        and all(isinstance(error, ProviderPlanError | ProviderLimitError) for error in refusals)
        and any(isinstance(error, ProviderPlanError) for error in refusals)
    ):
        # Every key that could answer refused, or has used its plan's requests for now.
        raise ProviderPlanError(
            f"None of your keys' plans covers {what} for {symbol} right now."
        ) from errors[-1]
    raise DataFetchError(f"Could not fetch {what} for {symbol!r}.") from errors[-1]


def _key_digest(api_key: str) -> str:
    """A short hash naming a key in recall keys, so keys never reach memory or Redis."""
    return usage.key_id(api_key)


def _now() -> float:
    """The monotonic clock refusals and pauses expire by."""
    return time.monotonic()


async def _recall(*keys: str) -> datetime | None:
    """When the first of ``keys`` that is a refusal or pause still in force, here or in
    Redis, ends; ``None`` when none is."""
    now = _now()
    for key in keys:
        entry = _recalled.get(key)
        if entry is not None:
            if entry[0] > now:
                return datetime.fromtimestamp(entry[1], timezone.utc)
            del _recalled[key]
    for key in keys:
        value = await cache_get(key)
        if value:
            try:
                ends = float(value)
            except ValueError:
                ends = time.time() + 60.0
            if ends < 1_000_000_000:  # Stored by a version that kept no end.
                ends = time.time() + 60.0
            _recalled[key] = (now + 60.0, ends)
            return datetime.fromtimestamp(ends, timezone.utc)
    return None


async def _remember(key: str, ttl_seconds: float) -> datetime:
    """Record a refusal or pause for ``ttl_seconds``, here and in Redis for every instance,
    and return when it ends."""
    ttl_seconds = max(1.0, ttl_seconds)
    ends = time.time() + ttl_seconds
    _recalled[key] = (_now() + ttl_seconds, ends)
    _recalled.move_to_end(key)
    while len(_recalled) > RECALL_MEMORY_SIZE:
        _recalled.popitem(last=False)
    await cache_set(key, repr(ends), ttl_seconds)
    return datetime.fromtimestamp(ends, timezone.utc)


def _seconds_until(provider: usage.UsageProvider, window: usage.Window) -> float:
    """Seconds until ``provider``'s ``window`` resets, as its plan's limits do."""
    now = datetime.now(timezone.utc)
    return (usage.window_end(provider, window, now) - now).total_seconds()


async def _forget(key: str) -> None:
    """Drop a recorded refusal, here and in Redis."""
    _recalled.pop(key, None)
    await cache_set(key, "", 0.001)


def _fmp_refusal_keys(path: str, symbol: str, api_key: str) -> tuple[str, str]:
    """Recall keys of FMP refusing ``path`` outright, and for ``symbol``, on this key."""
    base = f"fmp:refused:{_key_digest(api_key)}:{path}"
    return base, f"{base}:{symbol}"


async def forget_fmp_refusal(path: str, symbol: str, api_key: str) -> None:
    """Forget that FMP refused ``path`` for ``symbol``, as when the symbol turns out not to
    exist: FMP answers some requests for unknown symbols with a plan error."""
    await _forget(_fmp_refusal_keys(path, symbol, api_key)[1])


async def fmp_get(
    path: str, params: dict[str, str], api_key: str, client: httpx2.AsyncClient | None
) -> Any:
    """GET an FMP endpoint with the user's key, classifying plan and key errors.

    Refusals are remembered for :data:`FMP_REFUSAL_TTL_SECONDS`, for the symbol or, for an
    endpoint the plan excludes, for every symbol, and a reached limit leaves FMP out for
    :data:`FMP_LIMIT_PAUSE_SECONDS`; either is answered at once, without a request.
    """
    symbol = params.get("symbol", "")
    whole, one = _fmp_refusal_keys(path, symbol, api_key)
    paused = f"fmp:paused:{_key_digest(api_key)}"
    if (ends := await _recall(paused)) is not None:
        raise ProviderLimitError(
            "Your Financial Modeling Prep plan's limit was reached.", "fmp", ends
        )
    if await _recall(whole, one):
        raise ProviderPlanError(
            f"Your Financial Modeling Prep plan does not cover this request ({path} for "
            f"{symbol}).",
            remembered=True,
        )

    async def get(http: httpx2.AsyncClient) -> httpx2.Response:
        return await http.get(f"{FMP_BASE_URL}/{path}", params=params | {"apikey": api_key})

    if client is not None:
        response = await get(client)
    else:
        async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as own_client:
            response = await get(own_client)
    usage.record("fmp", api_key, size=len(response.content), headers=response.headers)
    if response.status_code == 402:
        # "Restricted Endpoint" refuses the endpoint; "Premium Query Parameter", the symbol.
        restricted = "restricted endpoint" in response.text.lower()
        await _remember(whole if restricted else one, FMP_REFUSAL_TTL_SECONDS)
        raise ProviderPlanError(
            f"Your Financial Modeling Prep plan does not cover this request ({path} for "
            f"{symbol})."
        )
    if response.status_code in (401, 403):
        raise ProviderNotConfiguredError(
            "Financial Modeling Prep rejected your API key; update it in Settings."
        )
    if response.status_code == 429:
        ends = await _remember(paused, FMP_LIMIT_PAUSE_SECONDS)
        raise ProviderLimitError(
            "Your Financial Modeling Prep plan's rate limit was reached.", "fmp", ends
        )
    response.raise_for_status()
    return response.json()


def finnhub_minute_limit(api_key: str) -> int | None:
    """The requests a minute that ``api_key``'s Finnhub plan allows, as Finnhub's
    ``X-Ratelimit-Limit`` header last said (60 on the free plan, up to 900 on paid ones),
    or ``None`` before it has answered recently. The header tells the plan the key is
    really on, so a paid plan is used at its own pace and no other key is."""
    report = usage.reported("finnhub", api_key)
    if report is None:
        return None
    for limit in report.limits:
        if limit.window == "minute" and limit.unit == "requests" and limit.limit:
            return min(limit.limit, FINNHUB_MOST_PER_MINUTE)
    return None


def finnhub_requests_per_minute(api_key: str, share: float = FINNHUB_PACE_SHARE) -> int:
    """The requests a minute PyQxel sends on ``api_key``: ``share`` of the limit Finnhub
    reports for it, or of the free plan's before it has reported one."""
    limit = finnhub_minute_limit(api_key) or FINNHUB_FREE_PER_MINUTE
    return max(1, math.floor(limit * share))


async def _finnhub_slot(api_key: str) -> None:
    """Count a request against the key's minute, refusing it beyond
    :func:`finnhub_requests_per_minute`. The count is shared through Redis when it is set."""
    now = time.time()
    minute, digest = int(now // 60), _key_digest(api_key)
    count = await cache_increment(f"finnhub:count:{digest}:{minute}", 60)
    if count is None:
        count = _finnhub_counts.get((digest, minute), 0) + 1
        _finnhub_counts[digest, minute] = count
        for stale in [entry for entry in _finnhub_counts if entry[1] < minute]:
            del _finnhub_counts[stale]
    known = finnhub_minute_limit(api_key) is not None
    allowed = finnhub_requests_per_minute(api_key) if known else FINNHUB_REQUESTS_PER_MINUTE
    if count > allowed:
        raise FinnhubRateLimitError(
            "Your Finnhub plan's rate limit was reached; try again shortly.",
            retry_after=60 - int(now % 60),
        )


async def finnhub_get(
    path: str, params: dict[str, str], api_key: str, client: httpx2.AsyncClient | None
) -> Any:
    """GET a Finnhub endpoint with the user's key, classifying key, plan, and limit errors.

    The key travels in a header rather than the URL, so it stays out of logs. A refusal
    is remembered for :data:`FINNHUB_REFUSAL_TTL_SECONDS` for the symbol, and a reached
    rate limit leaves Finnhub out for :data:`FINNHUB_LIMIT_PAUSE_SECONDS`; either is
    answered at once, without a request.
    """
    symbol = params.get("symbol", "")
    digest = _key_digest(api_key)
    refused, paused = f"finnhub:refused:{digest}:{path}:{symbol}", f"finnhub:paused:{digest}"
    if (ends := await _recall(paused)) is not None:
        raise FinnhubRateLimitError(
            "Your Finnhub plan's rate limit was reached; try again shortly.", resets_at=ends
        )
    if await _recall(refused):
        raise ProviderPlanError(
            f"Your Finnhub plan does not cover this request ({path} for {symbol}).",
            remembered=True,
        )
    await _finnhub_slot(api_key)

    async def get(http: httpx2.AsyncClient) -> httpx2.Response:
        return await http.get(
            f"{FINNHUB_URL}/{path}", params=params, headers={"X-Finnhub-Token": api_key}
        )

    try:
        if client is not None:
            response = await get(client)
        else:
            async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as own_client:
                response = await get(own_client)
    except httpx2.HTTPError as exc:
        raise DataFetchError("Finnhub could not be reached.") from exc
    usage.record("finnhub", api_key, size=len(response.content), headers=response.headers)
    if response.status_code == 401:
        raise ProviderNotConfiguredError("Finnhub rejected your API key; update it in Settings.")
    if response.status_code == 403:
        await _remember(refused, FINNHUB_REFUSAL_TTL_SECONDS)
        raise ProviderPlanError(
            f"Your Finnhub plan does not cover this request ({path} for {symbol})."
        )
    if response.status_code == 429:
        await _remember(paused, FINNHUB_LIMIT_PAUSE_SECONDS)
        value = response.headers.get("retry-after")
        raise FinnhubRateLimitError(
            "Your Finnhub plan's rate limit was reached; try again shortly.",
            int(value) if value is not None and value.isdigit() else None,
        )
    if response.status_code >= 400:
        raise DataFetchError(f"Finnhub returned an error ({response.status_code}).")
    try:
        return response.json()
    except ValueError as exc:
        raise DataFetchError("Finnhub returned an unreadable answer.") from exc


def tiingo_symbol(symbol: str) -> str:
    """``symbol`` as Tiingo names it: lower case, share classes with a dash, as in brk-b."""
    return symbol.strip().lower().replace(".", "-")


TIINGO_SYMBOL_PATHS: tuple[str, ...] = ("tiingo/daily/", "iex/")
"""Tiingo paths whose next segment is the ticker asked for."""


def tiingo_symbols(path: str, params: dict[str, str]) -> list[str]:
    """The tickers a Tiingo request names, which its plans count toward the different
    symbols a month they allow."""
    names = [name for name in params.get("tickers", "").split(",") if name]
    for prefix in TIINGO_SYMBOL_PATHS:
        if path.startswith(prefix):
            ticker = path[len(prefix) :].split("/", 1)[0]
            if ticker:
                names.append(ticker)
    return [name.upper() for name in names]


def _tiingo_detail(response: httpx2.Response) -> str:
    """The message in a Tiingo error answer, or its status."""
    try:
        body: Any = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        return str(body.get("detail") or body.get("message") or body)[:200]
    return str(body)[:200]


async def tiingo_get(
    path: str, params: dict[str, str], api_key: str, client: httpx2.AsyncClient | None = None
) -> Any:
    """GET a Tiingo endpoint with the user's key, classifying key, plan, and limit errors.

    The key travels in a header, so it stays out of logs. A refusal of the request is
    remembered for :data:`TIINGO_REFUSAL_TTL_SECONDS`, and a reached limit leaves Tiingo
    out for :data:`TIINGO_LIMIT_PAUSE_SECONDS`; either is answered at once, without a
    request.
    """
    digest = _key_digest(api_key)
    refused, paused = f"tiingo:refused:{digest}:{path}", f"tiingo:paused:{digest}"
    if (ends := await _recall(paused)) is not None:
        raise ProviderLimitError("Your Tiingo plan's limit was reached.", "tiingo", ends)
    if await _recall(refused):
        raise ProviderPlanError(
            f"Your Tiingo plan does not cover this request ({path}).", remembered=True
        )

    async def get(http: httpx2.AsyncClient) -> httpx2.Response:
        return await http.get(
            f"{TIINGO_URL}/{path}",
            params=params,
            headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
        )

    try:
        if client is not None:
            response = await get(client)
        else:
            async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as own_client:
                response = await get(own_client)
    except httpx2.HTTPError as exc:
        raise DataFetchError("Tiingo could not be reached.") from exc
    usage.record(
        "tiingo",
        api_key,
        symbols=tiingo_symbols(path, params),
        size=len(response.content),
        headers=response.headers,
    )
    if response.status_code < 400:
        try:
            return response.json()
        except ValueError as exc:
            raise DataFetchError("Tiingo returned an unreadable answer.") from exc
    detail = _tiingo_detail(response)
    lowered = detail.lower()
    if response.status_code == 404 or "not found" in lowered:
        raise SymbolNotFoundError(f"Tiingo has no data for this symbol ({path}).")
    if response.status_code == 429 or "limit" in lowered and "plan" not in lowered:
        window: usage.Window = (
            "month"
            if any(word in lowered for word in ("month", "symbol", "bandwidth"))
            else "day" if "dai" in lowered or " day" in lowered else "hour"
        )
        ends = await _remember(paused, _seconds_until("tiingo", window))
        raise ProviderLimitError(f"Your Tiingo plan's limit was reached: {detail}", "tiingo", ends)
    if response.status_code in (401, 403) and "plan" not in lowered and "subscri" not in lowered:
        raise ProviderNotConfiguredError("Tiingo rejected your API key; update it in Settings.")
    if response.status_code in (400, 402, 403):
        await _remember(refused, TIINGO_REFUSAL_TTL_SECONDS)
        raise ProviderPlanError(f"Your Tiingo plan does not cover this request: {detail}")
    raise DataFetchError(f"Tiingo returned an error ({response.status_code}).")


EXCHANGE_NAMES: dict[str, str] = {
    "L": "London Stock Exchange",
    "DE": "XETRA (Germany)",
    "F": "Frankfurt Stock Exchange",
    "PA": "Euronext Paris",
    "AS": "Euronext Amsterdam",
    "BR": "Euronext Brussels",
    "LS": "Euronext Lisbon",
    "MI": "Borsa Italiana (Milan)",
    "SW": "SIX Swiss Exchange",
    "MC": "Madrid Stock Exchange",
    "ST": "Nasdaq Stockholm",
    "OL": "Oslo Stock Exchange",
    "CO": "Nasdaq Copenhagen",
    "HE": "Nasdaq Helsinki",
    "IR": "Euronext Dublin",
    "VI": "Vienna Stock Exchange",
    "WA": "Warsaw Stock Exchange",
    "PR": "Prague Stock Exchange",
    "AT": "Athens Stock Exchange",
    "BD": "Budapest Stock Exchange",
    "IS": "Borsa Istanbul",
    "TO": "Toronto Stock Exchange",
    "V": "TSX Venture Exchange",
    "NE": "Cboe Canada",
    "MX": "Mexican Stock Exchange",
    "SA": "B3 (São Paulo)",
    "BA": "Buenos Aires Stock Exchange",
    "SN": "Santiago Stock Exchange",
    "T": "Tokyo Stock Exchange",
    "HK": "Hong Kong Stock Exchange",
    "SS": "Shanghai Stock Exchange",
    "SZ": "Shenzhen Stock Exchange",
    "KS": "Korea Exchange",
    "KQ": "KOSDAQ",
    "TW": "Taiwan Stock Exchange",
    "TWO": "Taipei Exchange",
    "NS": "National Stock Exchange of India",
    "BO": "BSE (Bombay Stock Exchange)",
    "SI": "Singapore Exchange",
    "JK": "Indonesia Stock Exchange",
    "BK": "Stock Exchange of Thailand",
    "KL": "Bursa Malaysia",
    "AX": "Australian Securities Exchange",
    "NZ": "New Zealand Exchange",
    "JO": "Johannesburg Stock Exchange",
    "TA": "Tel Aviv Stock Exchange",
    "SR": "Saudi Exchange",
}
"""The exchange each Yahoo-style symbol suffix names, for messages about it."""

SUFFIX_CURRENCIES: dict[str, str] = {
    "L": "GBP",
    "DE": "EUR",
    "F": "EUR",
    "PA": "EUR",
    "AS": "EUR",
    "BR": "EUR",
    "LS": "EUR",
    "MI": "EUR",
    "SW": "CHF",
    "MC": "EUR",
    "ST": "SEK",
    "OL": "NOK",
    "CO": "DKK",
    "HE": "EUR",
    "IR": "EUR",
    "VI": "EUR",
    "WA": "PLN",
    "PR": "CZK",
    "AT": "EUR",
    "BD": "HUF",
    "IS": "TRY",
    "TO": "CAD",
    "V": "CAD",
    "NE": "CAD",
    "MX": "MXN",
    "SA": "BRL",
    "BA": "ARS",
    "SN": "CLP",
    "T": "JPY",
    "HK": "HKD",
    "SS": "CNY",
    "SZ": "CNY",
    "KS": "KRW",
    "KQ": "KRW",
    "TW": "TWD",
    "TWO": "TWD",
    "NS": "INR",
    "BO": "INR",
    "SI": "SGD",
    "JK": "IDR",
    "BK": "THB",
    "KL": "MYR",
    "AX": "AUD",
    "NZ": "NZD",
    "JO": "ZAR",
    "TA": "ILS",
    "SR": "SAR",
}
"""The currency of each exchange in :data:`EXCHANGE_NAMES`, for figures a provider gives
in a listing's home currency. Totals such as a market cap are in the main unit (pounds,
rand, shekels), even where prices are quoted in its hundredths."""

EODHD_EXCHANGES: dict[str, str] = {
    "L": "LSE",
    "DE": "XETRA",
    "F": "F",
    "PA": "PA",
    "AS": "AS",
    "BR": "BR",
    "LS": "LS",
    "SW": "SW",
    "MC": "MC",
    "ST": "ST",
    "OL": "OL",
    "CO": "CO",
    "HE": "HE",
    "IR": "IR",
    "VI": "VI",
    "WA": "WAR",
    "PR": "PR",
    "AT": "AT",
    "BD": "BUD",
    "TO": "TO",
    "V": "V",
    "NE": "NEO",
    "MX": "MX",
    "SA": "SA",
    "BA": "BA",
    "SN": "SN",
    "SS": "SHG",
    "SZ": "SHE",
    "KS": "KO",
    "KQ": "KQ",
    "TW": "TW",
    "TWO": "TWO",
    "JK": "JK",
    "BK": "BK",
    "KL": "KLSE",
    "AX": "AU",
    "JO": "JSE",
}
"""EODHD's exchange code for each Yahoo-style symbol suffix whose exchange it lists, as its
exchange list read on November 5, 2026. It lists no exchange in Tokyo, Hong Kong, India,
Milan, Singapore, New Zealand, Istanbul, Tel Aviv, or Saudi Arabia, so those are not asked
of it."""

EODHD_CODES: frozenset[str] = frozenset(EODHD_EXCHANGES.values())
"""EODHD's own exchange codes, which a symbol may carry instead, as in ``SAP.XETRA``."""

ALPHA_VANTAGE_EXCHANGES: dict[str, str] = {
    "L": "LON",
    "DE": "DEX",
    "LS": "LIS",
    "TO": "TRT",
    "V": "TRV",
    "BO": "BSE",
    "SS": "SHH",
    "SZ": "SHZ",
}
"""Alpha Vantage's suffix for each Yahoo-style suffix whose exchange it covers, as its
documentation lists them (London, XETRA, Toronto, TSX Venture, BSE India, Shanghai, and
Shenzhen) and its search answered (Lisbon). A free key's quote of ``SAP.DEX`` answered on
November 5, 2026; ``7203.TYO`` and ``7203.T`` did not."""


def symbol_suffix(symbol: str) -> str | None:
    """The exchange suffix of a listing abroad, as ``L`` in ``VOD.L``; ``None`` for a US
    listing, an index, or an exchange rate."""
    symbol = symbol.strip().upper()
    if symbol_kind(symbol) != "international" or "." not in symbol:
        return None
    return symbol.rsplit(".", 1)[1]


def exchange_name(symbol: str) -> str:
    """The exchange a listing abroad trades on, for messages: "the Tokyo Stock Exchange"."""
    suffix = symbol_suffix(symbol)
    name = EXCHANGE_NAMES.get(suffix or "")
    return f"the {name}" if name else f"its exchange (.{suffix})" if suffix else "its exchange"


def eodhd_symbol(symbol: str) -> str:
    """``symbol`` as EODHD names it: AAPL.US, SHEL.LSE, GSPC.INDX, or EURUSD.FOREX."""
    symbol = symbol.strip().upper()
    if symbol.startswith("^"):
        return f"{symbol[1:]}.INDX"
    if symbol.endswith("=X"):
        return f"{symbol[:-2]}.FOREX"
    if "." in symbol:
        code, suffix = symbol.rsplit(".", 1)
        return f"{code}.{EODHD_EXCHANGES.get(suffix, suffix)}"
    return f"{symbol}.US"


def alpha_vantage_symbol(symbol: str) -> str:
    """``symbol`` as Alpha Vantage names it: a listing abroad takes its own suffix, as
    ``VOD.LON`` for ``VOD.L`` and ``SAP.DEX`` for ``SAP.DE``; others are unchanged."""
    symbol = symbol.strip().upper()
    suffix = symbol_suffix(symbol)
    if suffix is None or suffix not in ALPHA_VANTAGE_EXCHANGES:
        return symbol
    return f"{symbol.rsplit('.', 1)[0]}.{ALPHA_VANTAGE_EXCHANGES[suffix]}"


def provider_covers(provider: Provider, symbol: str) -> bool:
    """Whether ``provider`` lists ``symbol``'s exchange on any plan. A listing abroad is
    asked only of the providers that list its exchange, so no request, nor any of a free
    plan's few, is spent on one that cannot answer."""
    suffix = symbol_suffix(symbol)
    if suffix is None:
        return True
    if provider == "eodhd":
        return suffix in EODHD_EXCHANGES or suffix in EODHD_CODES
    if provider == "alpha_vantage":
        return suffix in ALPHA_VANTAGE_EXCHANGES
    if provider == "tiingo":
        # Tiingo's prices abroad cover Chinese A-shares alone, under names of its own.
        return False
    return True


async def eodhd_get(
    path: str, params: dict[str, str], api_key: str, client: httpx2.AsyncClient | None = None
) -> Any:
    """GET an EODHD endpoint with the user's key, classifying key, plan, and limit errors.

    EODHD takes the key only in the URL, so no error message repeats the URL. A refusal of
    the request, such as intraday bars on the free plan, is remembered for
    :data:`EODHD_REFUSAL_TTL_SECONDS`, and a reached daily limit leaves EODHD out for
    :data:`EODHD_LIMIT_PAUSE_SECONDS`; either is answered at once, without a request.
    """
    digest = _key_digest(api_key)
    endpoint = path.split("/", 1)[0]
    refused, paused = f"eodhd:refused:{digest}:{endpoint}", f"eodhd:paused:{digest}"
    if (ends := await _recall(paused)) is not None:
        raise ProviderLimitError("Your EODHD plan's daily limit was reached.", "eodhd", ends)
    if await _recall(refused):
        raise ProviderPlanError(
            f"Your EODHD plan does not cover this request ({endpoint}).", remembered=True
        )

    async def get(http: httpx2.AsyncClient) -> httpx2.Response:
        return await http.get(
            f"{EODHD_URL}/{path}", params=params | {"api_token": api_key, "fmt": "json"}
        )

    try:
        if client is not None:
            response = await get(client)
        else:
            async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as own_client:
                response = await get(own_client)
    except httpx2.HTTPError:
        raise DataFetchError("EODHD could not be reached.") from None
    usage.record("eodhd", api_key, size=len(response.content), headers=response.headers)
    text = response.text[:200].lower()
    if response.status_code == 404:
        raise SymbolNotFoundError(f"EODHD has no data for this symbol ({endpoint}).")
    if response.status_code == 401:
        raise ProviderNotConfiguredError("EODHD rejected your API key; update it in Settings.")
    if response.status_code == 429:
        retry = _finite(response.headers.get("retry-after"))
        ends = await _remember(paused, retry or 60.0)
        raise ProviderLimitError("Your EODHD plan's limit a minute was reached.", "eodhd", ends)
    if response.status_code == 402 or "exceeded" in text:
        ends = await _remember(paused, _seconds_until("eodhd", "day"))
        raise ProviderLimitError("Your EODHD plan's daily limit was reached.", "eodhd", ends)
    if response.status_code == 403:
        await _remember(refused, EODHD_REFUSAL_TTL_SECONDS)
        raise ProviderPlanError(f"Your EODHD plan does not cover this request ({endpoint}).")
    if response.status_code >= 400:
        raise DataFetchError(f"EODHD returned an error ({response.status_code}).")
    try:
        return response.json()
    except ValueError:
        raise DataFetchError("EODHD returned an unreadable answer.") from None


async def eodhd_account(api_key: str) -> dict[str, Any] | None:
    """EODHD's account details for the key: the calls spent on the latest day used
    (``apiRequests`` on ``apiRequestsDate``), the daily allowance (``dailyRateLimit``), and
    any extra calls bought (``extraLimit``). Asking costs no calls. ``None`` when EODHD
    cannot be reached or rejects the key."""
    try:
        async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{EODHD_URL}/user", params={"api_token": api_key, "fmt": "json"}
            )
    except httpx2.HTTPError:
        logger.info("EODHD's account details could not be read.")
        return None
    if response.status_code != 200:
        logger.info("EODHD answered its account details with HTTP %d.", response.status_code)
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    return {
        name: payload[name]
        for name in ("apiRequests", "apiRequestsDate", "dailyRateLimit", "extraLimit")
        if name in payload
    }


def _alpha_vantage_error(
    payload: Any, function: str, symbol: str, free: bool = False
) -> DataFetchError | None:
    """The error an Alpha Vantage answer reports, if any.

    Alpha Vantage answers every request with status 200 and reports problems in the body:
    ``Error Message`` for an unknown symbol, and ``Information`` or ``Note`` for a bad
    key, a rate limit, or a function the plan does not cover. It answers an invalid key as
    if it asked for a premium function, so for a ``free`` request that means a bad key.
    """
    if not isinstance(payload, dict):
        return DataFetchError(f"Alpha Vantage returned an unexpected {function} answer.")
    if "Error Message" in payload:
        return SymbolNotFoundError(f"Alpha Vantage has no {function} data for {symbol!r}.")
    message = str(payload.get("Information") or payload.get("Note") or "")
    if not message:
        return None
    lowered = message.lower()
    bad_key = "api key" in lowered and any(
        word in lowered for word in ("invalid", "missing", "demo")
    )
    if bad_key or ("premium" in lowered and free and "rate limit" not in lowered):
        return ProviderNotConfiguredError(
            "Alpha Vantage rejected your API key; update it in Settings."
        )
    if "burst" in lowered or "per second" in lowered:
        return AlphaVantageBurstError(f"Alpha Vantage refused a burst of requests: {message}")
    if "per day" in lowered or "daily" in lowered:
        return AlphaVantageDailyLimitError(
            f"Your Alpha Vantage plan's daily limit was reached: {message}", "alpha_vantage"
        )
    if "rate limit" in lowered or "requests per" in lowered or "call frequency" in lowered:
        return DataFetchError(f"Your Alpha Vantage plan's rate limit was reached: {message}")
    if "premium" in lowered:
        return ProviderPlanError(
            f"Your Alpha Vantage plan does not cover this request ({function} for {symbol})."
        )
    return DataFetchError(f"Alpha Vantage refused the {function} request: {message}")


async def _alpha_vantage_slot() -> None:
    """Wait for this request's turn, keeping requests :data:`ALPHA_VANTAGE_SPACING_SECONDS`
    apart. Turns are handed out without awaiting, so concurrent callers queue up."""
    global _alpha_vantage_next_slot
    now = time.monotonic()
    slot = max(now, _alpha_vantage_next_slot)
    _alpha_vantage_next_slot = slot + ALPHA_VANTAGE_SPACING_SECONDS
    if slot > now:
        await asyncio.sleep(slot - now)


def _remember_alpha_vantage(key: str, text: str) -> None:
    """Keep an answer in memory, dropping the least recently used beyond the limit."""
    _alpha_vantage_memory[key] = (time.monotonic(), text)
    _alpha_vantage_memory.move_to_end(key)
    while len(_alpha_vantage_memory) > ALPHA_VANTAGE_MEMORY_SIZE:
        _alpha_vantage_memory.popitem(last=False)


async def alpha_vantage_get(
    function: str, params: dict[str, str], api_key: str, client: httpx2.AsyncClient | None = None
) -> dict[str, Any]:
    """Call an Alpha Vantage function with the user's key, reusing recent answers.

    Answers, including those for unknown symbols, are cached in memory and Redis for
    :data:`ALPHA_VANTAGE_TTL_SECONDS`, keyed without the API key: like FMP data, they
    are only read on behalf of users who query Alpha Vantage themselves.

    Raises:
        SymbolNotFoundError: If Alpha Vantage has no data for the symbol.
        ProviderPlanError: If the user's plan does not cover ``function``.
        ProviderNotConfiguredError: If the key is rejected.
        DataFetchError: On rate limits, transport failures, and unexpected answers.
    """
    symbol = params.get("symbol") or params.get("from_currency", "")
    cache_key = "alphavantage:" + ":".join(
        [function, *(f"{name}={value}" for name, value in sorted(params.items()))]
    )
    ttl = ALPHA_VANTAGE_TTL_SECONDS.get(function, HISTORY_TTL_SECONDS)
    digest = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    request = f"{function}:{params.get('outputsize', '')}"
    free = function in ALPHA_VANTAGE_FREE_FUNCTIONS
    paused = f"alphavantage:paused:{digest}"
    refused = _alpha_vantage_premium.get((digest, request))
    if refused is not None and time.monotonic() - refused < ALPHA_VANTAGE_PLAN_TTL_SECONDS:
        raise ProviderPlanError(
            f"Your Alpha Vantage plan does not cover this request ({function} for {symbol})."
        )

    text: str | None = None
    entry = _alpha_vantage_memory.get(cache_key)
    if entry is not None and time.monotonic() - entry[0] <= ttl:
        text = entry[1]
    if text is None:
        text = await cache_get(cache_key)
        if text is not None:
            _remember_alpha_vantage(cache_key, text)
    payload: Any = None
    if text is not None:
        try:
            payload = json.loads(text)
        except ValueError:
            logger.warning("Ignoring an unreadable cached Alpha Vantage %s answer.", function)
    if payload is None:
        if (ends := await _recall(paused)) is not None:
            raise AlphaVantageDailyLimitError(
                "Your Alpha Vantage plan's daily limit was reached.", "alpha_vantage", ends
            )
        query = {"function": function, **params, "apikey": api_key}
        for attempt in range(2):
            await _alpha_vantage_slot()
            try:
                if client is not None:
                    response = await client.get(ALPHA_VANTAGE_URL, params=query)
                else:
                    async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as own_client:
                        response = await own_client.get(ALPHA_VANTAGE_URL, params=query)
                usage.record(
                    "alpha_vantage", api_key, size=len(response.content), headers=response.headers
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx2.HTTPError, ValueError) as exc:
                raise DataFetchError(f"Alpha Vantage {function} request failed: {exc}") from exc
            error = _alpha_vantage_error(payload, function, symbol, free)
            # Another process may have sent a request just before; one retry waits it out.
            if not isinstance(error, AlphaVantageBurstError):
                break
        if isinstance(error, ProviderPlanError):
            _alpha_vantage_premium[digest, request] = time.monotonic()
        if isinstance(error, AlphaVantageDailyLimitError):
            error.provider = "alpha_vantage"
            error.resets_at = await _remember(paused, ALPHA_VANTAGE_LIMIT_PAUSE_SECONDS)
        if error is None or isinstance(error, SymbolNotFoundError):
            text = json.dumps(payload)
            _remember_alpha_vantage(cache_key, text)
            await cache_set(cache_key, text, ttl)
    error = _alpha_vantage_error(payload, function, symbol, free)
    if error is not None:
        raise error
    result: dict[str, Any] = payload
    return result


def alpha_vantage_free_history(api_key: str) -> bool:
    """Whether ``api_key``'s Alpha Vantage plan is known to serve only the latest
    :data:`ALPHA_VANTAGE_FREE_DAILY_BARS` daily bars, as the free plan does: it refused the
    full adjusted series within the last :data:`ALPHA_VANTAGE_PLAN_TTL_SECONDS`."""
    digest = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    refused = _alpha_vantage_premium.get((digest, "TIME_SERIES_DAILY_ADJUSTED:full"))
    return refused is not None and time.monotonic() - refused < ALPHA_VANTAGE_PLAN_TTL_SECONDS


def no_longer_history(
    keys: dict[Provider, str | None],
) -> Callable[[Provider, pd.DataFrame | None], bool]:
    """For :func:`first_success`: whether a provider is known not to serve more daily bars
    than a partial history already found, among the providers in ``keys``."""

    def check(provider: Provider, found: pd.DataFrame | None) -> bool:
        api_key = keys.get(provider)
        return (
            provider == "alpha_vantage"
            and api_key is not None
            and found is not None
            and len(found) >= ALPHA_VANTAGE_FREE_DAILY_BARS
            and alpha_vantage_free_history(api_key)
        )

    return check


def clear_alpha_vantage_memory() -> None:
    """Forget Alpha Vantage answers, plan refusals, and request spacing kept in memory, and
    every provider's refusals, pauses, and request counts."""
    global _alpha_vantage_next_slot
    _alpha_vantage_memory.clear()
    _alpha_vantage_premium.clear()
    _alpha_vantage_next_slot = 0.0
    _recalled.clear()
    _finnhub_counts.clear()
    _tiingo_meta_memory.clear()
    _store_memory.clear()
    _store_locks.clear()


def alpha_vantage_number(value: object) -> float | None:
    """An Alpha Vantage figure as a float; it writes missing ones as ``"None"`` or ``"-"``."""
    if value is None or value in ("None", "-", ""):
        return None
    return _finite(value)


async def _alpha_vantage_overview(
    symbol: str, api_key: str, client: httpx2.AsyncClient | None
) -> TickerInfo:
    """Fetch ticker info from Alpha Vantage's company overview, which needs a paid plan.

    The overview has no price; callers needing one ask for a quote.
    """
    overview = await alpha_vantage_get("OVERVIEW", {"symbol": symbol}, api_key, client)
    if not overview.get("Symbol"):
        raise SymbolNotFoundError(f"Alpha Vantage has no overview for {symbol!r}.")

    def text(field: str) -> str | None:
        value = overview.get(field)
        return None if value in (None, "None", "-", "") else str(value)

    sector, industry = text("Sector"), text("Industry")
    market_cap = alpha_vantage_number(overview.get("MarketCapitalization"))
    return TickerInfo(
        symbol=symbol,
        name=text("Name"),
        currency=text("Currency"),
        exchange=text("Exchange"),
        sector=None if sector is None else sector.title(),
        industry=None if industry is None else industry.title(),
        market_cap=market_cap if market_cap and market_cap > 0 else None,
        source="alpha_vantage",
    )


async def alpha_vantage_name(
    symbol: str, api_key: str, client: httpx2.AsyncClient | None = None
) -> tuple[str, str | None]:
    """The name and currency of ``symbol``'s listing, from Alpha Vantage's symbol search.

    Raises:
        SymbolNotFoundError: If no listing has exactly ``symbol`` as its symbol.
    """
    named = alpha_vantage_symbol(symbol)
    payload = await alpha_vantage_get("SYMBOL_SEARCH", {"keywords": named}, api_key, client)
    for match in payload.get("bestMatches") or []:
        if isinstance(match, dict) and str(match.get("1. symbol", "")).upper() == named:
            currency = match.get("8. currency")
            return str(match.get("2. name") or symbol), None if not currency else str(currency)
    raise SymbolNotFoundError(f"Alpha Vantage lists no {symbol!r}.")


async def _alpha_vantage_info(
    symbol: str, api_key: str, client: httpx2.AsyncClient | None
) -> TickerInfo:
    """Fetch ticker info from Alpha Vantage.

    With a paid plan, the company overview gives sector, industry, and market cap. A free
    key gets the name and currency from the symbol search and the price from a quote, so
    valuation estimates the market cap from shares outstanding.
    """
    try:
        return await _alpha_vantage_overview(symbol, api_key, client)
    except ProviderPlanError:
        keys = current_provider_keys()
        if keys.finnhub or keys.fmp or keys.tiingo or keys.eodhd:
            # A free plan's profile costs two of its 25 requests a day, which its financial
            # statements need more; the user's other keys answer profiles again soon.
            raise ProviderPlanError(
                "Alpha Vantage's free plan gives a company profile only for two of its 25 "
                "requests a day, so it is left to your other keys."
            ) from None
    name, currency = await alpha_vantage_name(symbol, api_key, client)
    quote = await _alpha_vantage_quote(symbol, api_key, client)
    return TickerInfo(
        symbol=symbol, name=name, currency=currency, price=quote.price, source="alpha_vantage"
    )


def _positive(value: object) -> float | None:
    """``value`` as a float when it is positive; Finnhub answers zero for missing figures."""
    number = _finite(value)
    return number if number is not None and number > 0 else None


async def _finnhub_profile(
    symbol: str, api_key: str, client: httpx2.AsyncClient | None
) -> TickerInfo:
    """A company's profile from Finnhub, without a price, reused for a day."""
    key = f"finnhub:profile:{symbol}"
    cached = await cache_get(key)
    if cached is not None:
        try:
            return TickerInfo.model_validate_json(cached)
        except ValueError:
            logger.warning("Ignoring an unreadable cached Finnhub profile for %s.", symbol)
    payload = await finnhub_get(
        "stock/profile2", {"symbol": finnhub_symbol(symbol)}, api_key, client
    )
    if not isinstance(payload, dict) or not payload.get("name"):
        raise SymbolNotFoundError(f"Finnhub has no profile for {symbol!r}.")
    market_cap = _positive(payload.get("marketCapitalization"))
    # Finnhub gives the market cap in millions, in the currency of the company's home
    # listing.
    market_cap = None if market_cap is None else market_cap * 1e6
    currency = payload.get("currency") or None
    exchange = payload.get("exchange") or None
    home = str(payload.get("ticker") or "").upper()
    if symbol_kind(symbol) == "us" and symbol_suffix(home) is not None:
        # A depositary receipt, such as ASML for ASML.AS: Finnhub describes the home
        # listing, so the price is in dollars, on a US exchange, and the market cap is
        # converted from the home currency.
        if market_cap is not None and currency not in (None, "USD"):
            market_cap = await _in_dollars(market_cap, currency)
        currency, exchange = "USD", None
    profile = TickerInfo(
        symbol=symbol,
        name=payload.get("name") or None,
        currency=currency,
        exchange=exchange,
        industry=payload.get("finnhubIndustry") or None,
        market_cap=market_cap,
        quote_type="EQUITY",
        source="finnhub",
    )
    await cache_set(key, profile.model_dump_json(), FINNHUB_PROFILE_TTL_SECONDS)
    return profile


async def _in_dollars(amount: float, currency: str) -> float | None:
    """``amount`` of ``currency`` in US dollars at the latest reference rate; ``None`` when
    no rate can be found."""
    from app.data.exchange_rates import fetch_exchange_rate  # It imports this module.

    try:
        return amount * await fetch_exchange_rate(currency, "USD")
    except DataFetchError as exc:
        logger.info("No %s rate to convert a market cap: %s", currency, exc)
        return None


async def _finnhub_info(symbol: str, api_key: str, client: httpx2.AsyncClient | None) -> TickerInfo:
    """Ticker info from Finnhub's company profile and quote."""
    profile = await _finnhub_profile(symbol, api_key, client)
    try:
        quote = await _finnhub_quote(symbol, api_key, client)
    except DataFetchError as exc:
        logger.info("Finnhub has a profile but no quote for %s: %s", symbol, exc)
        return profile
    return profile.model_copy(update={"price": quote.price})


async def _tiingo_meta(
    symbol: str, api_key: str, client: httpx2.AsyncClient | None
) -> dict[str, Any]:
    """Tiingo's company meta for ``symbol``: its name for any US ticker, and its sector and
    industry, which the free plan fills in for the Dow 30 alone; a field it withholds, which
    it answers with a "not available" placeholder, is left out."""
    key = f"tiingo:meta:{symbol}"
    cached = _tiingo_meta_memory.get(key)
    if cached is not None and time.monotonic() - cached[0] < TIINGO_META_TTL_SECONDS:
        return dict(cached[1])
    text = await cache_get(key)
    if text is not None:
        try:
            stored: dict[str, Any] = json.loads(text)
            _tiingo_meta_memory[key] = (time.monotonic(), stored)
            return dict(stored)
        except ValueError:
            logger.warning("Ignoring an unreadable cached Tiingo profile for %s.", symbol)
    rows = await tiingo_get(
        "tiingo/fundamentals/meta", {"tickers": tiingo_symbol(symbol)}, api_key, client
    )
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise SymbolNotFoundError(f"Tiingo has no company profile for {symbol!r}.")
    meta: dict[str, Any] = {
        field: value
        for field, value in rows[0].items()
        if not (isinstance(value, str) and "not available" in value.lower())
    }
    if len(meta) < len(rows[0]):
        # Remembered, so other stocks' sectors are not asked for on this key for a day.
        await _remember(f"tiingo:withheld:{_key_digest(api_key)}", TIINGO_META_TTL_SECONDS)
    _tiingo_meta_memory[key] = (time.monotonic(), meta)
    while len(_tiingo_meta_memory) > ALPHA_VANTAGE_MEMORY_SIZE:
        _tiingo_meta_memory.pop(next(iter(_tiingo_meta_memory)))
    await cache_set(key, json.dumps(meta, default=str), TIINGO_META_TTL_SECONDS)
    return meta


TIINGO_EXCHANGES: dict[str, str] = {
    "NASDAQ": "NASDAQ",
    "NYSE": "NYSE",
    "NYSE ARCA": "NYSEArca",
    # Nasdaq's quotation service for mutual funds, which trade with the fund, not on it.
    "NMFQS": "",
}
"""Exchange names for Tiingo's exchange codes, as the other providers write them."""


TIINGO_ASSET_TYPES: dict[str, str] = {"Stock": "EQUITY", "ETF": "ETF", "Mutual Fund": "MUTUALFUND"}
"""Yahoo-style quote types for the ``assetType`` of Tiingo's search, as the app uses them."""


async def _tiingo_quote_type(
    symbol: str, api_key: str, client: httpx2.AsyncClient | None
) -> str | None:
    """Whether a US listing is a stock, an ETF, a mutual fund, or a money-market fund, from
    Tiingo's search, which its free plan answers for any ticker; reused for a week, since
    it does not change."""
    key = f"tiingo:type:{symbol}"
    cached = _tiingo_meta_memory.get(key)
    if cached is not None and time.monotonic() - cached[0] < US_LISTING_TTL_SECONDS:
        return cached[1].get("type")
    text = await cache_get(key)
    if text is not None:
        found: str | None = text or None
        _tiingo_meta_memory[key] = (time.monotonic(), {"type": found})
        return found
    ticker = tiingo_symbol(symbol)
    rows = await tiingo_get(
        "tiingo/utilities/search", {"query": ticker, "limit": "10"}, api_key, client
    )
    quote_type: str | None = None
    for row in rows if isinstance(rows, list) else []:
        if (
            isinstance(row, dict)
            and str(row.get("ticker") or "").lower() == ticker
            and row.get("countryCode") in (None, "US")
        ):
            quote_type = TIINGO_ASSET_TYPES.get(str(row.get("assetType") or ""))
            if quote_type == "MUTUALFUND" and "MONEY MARKET" in str(row.get("name") or "").upper():
                quote_type = MONEY_MARKET_TYPE
            break
    _tiingo_meta_memory[key] = (time.monotonic(), {"type": quote_type})
    await cache_set(key, quote_type or "", US_LISTING_TTL_SECONDS)
    return quote_type


MONEY_MARKET_TYPE: str = "MONEYMARKET"
"""The quote type of a money-market fund, which the portfolio counts as cash."""

FUND_TYPES: frozenset[str] = frozenset({"ETF", "MUTUALFUND", MONEY_MARKET_TYPE})
"""Quote types of funds, which hold other securities rather than run a business: a
company's figures, such as its market cap, sector, or earnings, do not apply to them."""


async def _tiingo_info(symbol: str, api_key: str, client: httpx2.AsyncClient | None) -> TickerInfo:
    """Fetch the name, exchange and kind (stock, ETF, or fund) of any US listing Tiingo
    prices, with a stock's sector and industry where the plan gives them (the Dow 30, on
    the free plan); prices and market caps are left to other providers."""
    listing = await tiingo_get(f"tiingo/daily/{tiingo_symbol(symbol)}", {}, api_key, client)
    if not isinstance(listing, dict) or not listing.get("ticker"):
        raise SymbolNotFoundError(f"Tiingo has no listing {symbol!r}.")
    try:
        quote_type = await _tiingo_quote_type(symbol, api_key, client)
    except DataFetchError as exc:
        logger.info("Tiingo could not tell what kind of listing %s is: %s", symbol, exc)
        quote_type = None
    meta: dict[str, Any] = {}
    if quote_type not in FUND_TYPES:
        # A fund has no sector of its own, so its company meta would cost a request for
        # nothing.
        try:
            meta = await _tiingo_meta(symbol, api_key, client)
        except DataFetchError:
            meta = {}
    exchange = str(listing.get("exchangeCode") or "")
    return TickerInfo(
        symbol=symbol,
        name=listing.get("name") or meta.get("name") or None,
        currency="USD",
        exchange=TIINGO_EXCHANGES.get(exchange.upper(), exchange) or None,
        sector=meta.get("sector") or None,
        industry=meta.get("industry") or None,
        quote_type=quote_type,
        source="tiingo",
    )


EODHD_QUOTE_TYPES: dict[str, str] = {
    "common stock": "EQUITY",
    "preferred stock": "EQUITY",
    "etf": "ETF",
    "fund": "MUTUALFUND",
    "mutual fund": "MUTUALFUND",
    "index": "INDEX",
}
"""Yahoo-style quote types for EODHD's ``Type``, as the rest of the app uses them."""


async def _eodhd_info(symbol: str, api_key: str, client: httpx2.AsyncClient | None) -> TickerInfo:
    """Fetch a profile from EODHD: its fundamentals on a paid plan, with sector, industry, and
    market cap; else, on the free plan, its search for the name, exchange, currency, and
    type, or for an index, which search leaves out, its delayed quote."""
    try:
        return await _eodhd_fundamentals_info(symbol, api_key, client)
    except ProviderPlanError:
        pass
    code = eodhd_symbol(symbol)
    ticker, exchange = code.rsplit(".", 1)
    found = await eodhd_get(f"search/{ticker}", {"limit": "20"}, api_key, client)
    for row in found if isinstance(found, list) else []:
        if isinstance(row, dict) and row.get("Code") == ticker and row.get("Exchange") == exchange:
            return TickerInfo(
                symbol=symbol,
                name=row.get("Name") or None,
                currency=row.get("Currency") or None,
                exchange=row.get("Exchange") or None,
                price=_positive(_finite(row.get("previousClose"))),
                quote_type=EODHD_QUOTE_TYPES.get(str(row.get("Type") or "").lower()),
                source="eodhd",
            )
    quote = await _eodhd_quote(symbol, api_key, client)
    return TickerInfo(
        symbol=symbol,
        price=quote.price,
        quote_type="INDEX" if symbol_kind(symbol) == "index" else None,
        source="eodhd",
    )


async def _eodhd_fundamentals_info(
    symbol: str, api_key: str, client: httpx2.AsyncClient | None
) -> TickerInfo:
    """A company profile from EODHD's fundamentals, which need a paid plan; a free key's
    refusal is remembered."""
    payload = await eodhd_get(
        f"fundamentals/{eodhd_symbol(symbol)}",
        {"filter": "General,Highlights::MarketCapitalization"},
        api_key,
        client,
    )
    general: dict[str, Any] = (
        (payload or {}).get("General") or {} if isinstance(payload, dict) else {}
    )
    if not general:
        raise SymbolNotFoundError(f"EODHD has no company profile for {symbol!r}.")
    cap = payload.get("Highlights::MarketCapitalization") if isinstance(payload, dict) else None
    return TickerInfo(
        symbol=symbol,
        name=general.get("Name") or None,
        currency=general.get("CurrencyCode") or None,
        exchange=general.get("Exchange") or None,
        sector=general.get("Sector") or None,
        industry=general.get("Industry") or None,
        market_cap=_positive(_finite(cap)),
        quote_type=EODHD_QUOTE_TYPES.get(str(general.get("Type") or "").lower()),
        source="eodhd",
    )


TIINGO_GAP_FIELDS: tuple[str, ...] = ("name", "sector", "industry")
"""Ticker info fields Tiingo's free company meta fills in when another provider leaves them
empty."""

FINNHUB_GAP_FIELDS: tuple[str, ...] = ("name", "currency", "exchange", "industry", "market_cap")
"""Ticker info fields Finnhub's free company profile fills in when another provider leaves
them empty."""

INFO_GAP_FIELDS: tuple[str, ...] = (
    "name",
    "currency",
    "exchange",
    "sector",
    "industry",
    "market_cap",
)
"""Ticker info fields Alpha Vantage's overview fills in when another provider leaves them
empty. The overview needs a paid plan; a free key's refusal is remembered for a day, so it
costs one request a day."""


async def _fill_from_finnhub(info: TickerInfo, client: httpx2.AsyncClient | None) -> TickerInfo:
    """Fill the fields ``info``'s provider left empty from Finnhub's profile, if the user can.

    A market cap is filled only in the currency ``info`` is priced in.
    """
    api_key = finnhub_key()
    missing = [field for field in FINNHUB_GAP_FIELDS if getattr(info, field) is None]
    if api_key is None or info.source == "finnhub" or not missing:
        return info
    try:
        profile = await _finnhub_profile(info.symbol, api_key, client)
    except DataFetchError as exc:
        logger.info("Finnhub could not fill %s's info: %s", info.symbol, exc)
        return info
    if info.currency is not None and profile.currency != info.currency:
        missing = [field for field in missing if field != "market_cap"]
    filled = {field: value for field in missing if (value := getattr(profile, field)) is not None}
    if not filled:
        return info
    return info.model_copy(
        update=filled | {"filled_fields": [*info.filled_fields, *sorted(filled)]}
    )


async def _fill_from_tiingo(info: TickerInfo, client: httpx2.AsyncClient | None) -> TickerInfo:
    """Fill the name, sector, and industry ``info``'s provider left empty from Tiingo's meta,
    for a US listing, if the user has a Tiingo key."""
    key = current_provider_keys().tiingo
    missing = [field for field in TIINGO_GAP_FIELDS if getattr(info, field) is None]
    if key is None or info.source == "tiingo" or not missing or symbol_kind(info.symbol) != "us":
        return info
    secret = key.get_secret_value()
    withheld = f"tiingo:withheld:{_key_digest(secret)}"
    cached = f"tiingo:meta:{info.symbol}" in _tiingo_meta_memory
    if not cached and set(missing) <= {"sector", "industry"} and await _recall(withheld):
        # The plan withholds sectors outside the Dow 30, so asking would spend a request
        # for nothing.
        return info
    try:
        meta = await _tiingo_meta(info.symbol, secret, client)
    except DataFetchError as exc:
        logger.info("Tiingo could not fill %s's info: %s", info.symbol, exc)
        return info
    filled = {field: value for field in missing if (value := meta.get(field))}
    if not filled:
        return info
    return info.model_copy(
        update=filled | {"filled_fields": [*info.filled_fields, *sorted(filled)]}
    )


SEC_INDUSTRY_TTL_SECONDS: float = 30 * 24 * 60 * 60
"""How long a company's SEC industry code is reused; companies rarely change it."""
_sec_industries: dict[str, tuple[float, tuple[int | None, str | None]]] = {}
"""Symbol -> (time read, (SIC code, its title)), for deployments without Redis."""


async def _sec_industry(symbol: str) -> tuple[int | None, str | None]:
    """The SIC code and title the SEC files ``symbol``'s company under, reused for a month;
    public data, so one copy serves every account."""
    from app.data.sec_edgar import cik_for, edgar_client, fetch_industry_code

    remembered = _sec_industries.get(symbol)
    if remembered is not None and time.monotonic() - remembered[0] < SEC_INDUSTRY_TTL_SECONDS:
        return remembered[1]
    key = f"sec:industry:{symbol}"
    cached = await cache_get(key)
    if cached is not None:
        try:
            code, title = json.loads(cached)
            _sec_industries[symbol] = (time.monotonic(), (code, title))
            return code, title
        except ValueError, TypeError:
            logger.warning("Ignoring an unreadable cached SEC industry for %s.", symbol)
    async with edgar_client() as client:
        found = await fetch_industry_code(client, await cik_for(symbol, client))
    _sec_industries[symbol] = (time.monotonic(), found)
    await cache_set(key, json.dumps(found), SEC_INDUSTRY_TTL_SECONDS)
    return found


async def _fill_from_sec(info: TickerInfo) -> TickerInfo:
    """Fill a US company's missing sector and industry from the industry (SIC) code it
    files under with the SEC: public data, needing no key, so the keyed providers are
    asked only for what it leaves empty."""
    if symbol_kind(info.symbol) != "us" or (info.sector and info.industry):
        return info
    from app.stats.sectors import industry_name, sector_for_sic

    try:
        code, title = await _sec_industry(info.symbol)
    except Exception as exc:  # The SEC is a bonus here; the providers can still fill in.
        logger.info("The SEC has no industry for %s: %s", info.symbol, exc)
        return info
    found = {"sector": sector_for_sic(code), "industry": industry_name(title)}
    filled = {f: value for f, value in found.items() if value and getattr(info, f) is None}
    if not filled:
        return info
    return info.model_copy(
        update=filled | {"filled_fields": [*info.filled_fields, *sorted(filled)]}
    )


async def _fill_info(info: TickerInfo, client: httpx2.AsyncClient | None) -> TickerInfo:
    """Fill the fields ``info``'s provider left empty from the SEC's public industry codes,
    then Finnhub, Tiingo, and Alpha Vantage, if the user has keys for them. A named fund is
    left as it is: the fields it lacks, such as a sector or a market cap, are a company's,
    which no provider has for it."""
    if info.quote_type in FUND_TYPES and info.name:
        return info
    info = await _fill_from_sec(info)
    info = await _fill_from_finnhub(info, client)
    info = await _fill_from_tiingo(info, client)
    api_key = alpha_vantage_key()
    missing = [field for field in INFO_GAP_FIELDS if getattr(info, field) is None]
    if api_key is None or info.source == "alpha_vantage" or not missing:
        return info
    try:
        overview = await _alpha_vantage_overview(info.symbol, api_key, client)
    except DataFetchError as exc:
        logger.info("Alpha Vantage could not fill %s's info: %s", info.symbol, exc)
        return info
    filled = {field: value for field in missing if (value := getattr(overview, field)) is not None}
    if not filled:
        return info
    return info.model_copy(
        update=filled | {"filled_fields": [*info.filled_fields, *sorted(filled)]}
    )


def _yfinance_info(symbol: str) -> TickerInfo:
    """Fetch ticker info from yfinance (blocking)."""
    info: dict[str, Any] = yf.Ticker(symbol).info or {}
    if not info or info.get("quoteType") in (None, "NONE"):
        raise SymbolNotFoundError(f"yfinance has no quote for {symbol!r}.")
    return TickerInfo(
        symbol=symbol,
        name=info.get("longName") or info.get("shortName"),
        currency=info.get("currency"),
        exchange=info.get("exchange"),
        sector=info.get("sector"),
        industry=info.get("industry"),
        market_cap=info.get("marketCap"),
        price=info.get("currentPrice") or info.get("regularMarketPrice"),
        quote_type=info.get("quoteType"),
        source="yfinance",
    )


async def _fmp_info(symbol: str, api_key: str, client: httpx2.AsyncClient | None) -> TickerInfo:
    """Fetch ticker info from the FMP company profile endpoint."""
    payload = await fmp_get("profile", {"symbol": symbol}, api_key, client)
    if not isinstance(payload, list) or not payload:
        raise SymbolNotFoundError(f"FMP has no profile for {symbol!r}.")
    profile: dict[str, Any] = payload[0]
    return TickerInfo(
        symbol=symbol,
        name=profile.get("companyName"),
        currency=profile.get("currency"),
        exchange=profile.get("exchange") or profile.get("exchangeShortName"),
        sector=profile.get("sector") or None,
        industry=profile.get("industry") or None,
        market_cap=profile.get("marketCap") or profile.get("mktCap"),
        price=profile.get("price"),
        quote_type=(
            "ETF"
            if profile.get("isEtf")
            else "MUTUALFUND" if profile.get("isFund") else "EQUITY" if profile else None
        ),
        source="fmp",
    )


async def fetch_ticker_info(symbol: str, client: httpx2.AsyncClient | None = None) -> TickerInfo:
    """Fetch a descriptive and pricing snapshot for ``symbol``.

    Args:
        symbol: Ticker symbol, e.g. ``"AAPL"``. Case and surrounding whitespace are ignored.
        client: Optional shared HTTP client for FMP. A short-lived one is created when
            omitted.

    Returns:
        The normalized ticker snapshot. Fields its provider left empty are filled from
        Finnhub and Alpha Vantage when the user has keys for them, and named in
        ``filled_fields``.

    Raises:
        ValueError: If ``symbol`` is empty.
        ProviderNotConfiguredError: If the user has no market data key and the fallback
            is off.
        ProviderPlanError: If the user's FMP plan does not cover ``symbol``.
        SymbolNotFoundError: If the providers that answered have no data for ``symbol``.
        DataFetchError: If every provider fails.
    """
    symbol = _normalize_symbol(symbol)

    def attempt(provider: Provider, api_key: str | None) -> Callable[[], Awaitable[TickerInfo]]:
        async def run() -> TickerInfo:
            key = f"info:{provider}:{symbol}"
            cached = await cache_get(key)
            if cached is not None:
                try:
                    return TickerInfo.model_validate_json(cached)
                except ValueError:
                    logger.warning("Ignoring unreadable cached info for %s.", symbol)
            if provider == "fmp":
                assert api_key is not None
                info = await _fmp_info(symbol, api_key, client)
            elif provider == "finnhub":
                assert api_key is not None
                info = await _finnhub_info(symbol, api_key, client)
            elif provider == "alpha_vantage":
                assert api_key is not None
                info = await _alpha_vantage_info(symbol, api_key, client)
            elif provider == "tiingo":
                assert api_key is not None
                info = await _tiingo_info(symbol, api_key, client)
            elif provider == "eodhd":
                assert api_key is not None
                info = await _eodhd_info(symbol, api_key, client)
            else:
                info = await asyncio.to_thread(_yfinance_info, symbol)
            await cache_set(key, info.model_dump_json(), INFO_TTL_SECONDS)
            return info

        return run

    try:
        info = await first_success(
            symbol,
            "ticker info",
            [(p, attempt(p, k)) for p, k in available_providers("info", symbol)],
        )
    except ProviderPlanError as exc:
        hint = await plan_hint("info", symbol)
        if not hint:
            raise
        raise ProviderPlanError(f"{exc} {hint}") from exc
    info = await _fill_info(info, client)
    if info.price is None and info.quote_type != MONEY_MARKET_TYPE:
        # A profile from a provider that gives no price, as Tiingo's for an ETF, takes
        # the price from the cheapest quote the user's keys can get.
        try:
            quote = await fetch_quote(symbol, client)
        except DataFetchError as exc:
            logger.info("No quote for %s's price either: %s", symbol, exc)
        else:
            info = info.model_copy(
                update={"price": quote.price, "filled_fields": [*info.filled_fields, "price"]}
            )
    return info


def _yfinance_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Fetch OHLCV history from yfinance (blocking)."""
    frame: pd.DataFrame = yf.Ticker(symbol).history(
        period=period, interval=interval, auto_adjust=True
    )
    return frame


def exchange_timezone(symbol: str) -> str:
    """The time zone ``symbol`` trades in, judged by its Yahoo-style suffix."""
    for suffix, zone in SUFFIX_TIMEZONES.items():
        if symbol.endswith(suffix):
            return zone
    return DEFAULT_TIMEZONE


def _window_start(period: str, today: date) -> date:
    """The first calendar date a yfinance-style ``period`` covers, with room for holidays."""
    offsets: dict[str, pd.DateOffset] = {
        "1d": pd.DateOffset(days=7),
        "5d": pd.DateOffset(days=14),
        "1mo": pd.DateOffset(months=1),
        "3mo": pd.DateOffset(months=3),
        "6mo": pd.DateOffset(months=6),
        "1y": pd.DateOffset(years=1),
        "2y": pd.DateOffset(years=2),
        "5y": pd.DateOffset(years=5),
        "10y": pd.DateOffset(years=10),
    }
    if period == "ytd":
        return date(today.year, 1, 1)
    if period == "max":
        return date(1900, 1, 1)
    start: date = (pd.Timestamp(today) - offsets.get(period, pd.DateOffset(years=1))).date()
    return start


def _fmp_frame(rows: list[dict[str, Any]], fields: dict[str, str], zone: str) -> pd.DataFrame:
    """Build an OHLCV frame from FMP rows, stamped in the exchange's time zone."""
    if not rows:
        return pd.DataFrame(columns=OHLCV_COLUMNS, index=pd.DatetimeIndex([]), dtype=float)
    frame = pd.DataFrame(rows)
    index = pd.DatetimeIndex(pd.to_datetime(frame["date"])).tz_localize(zone)
    columns = {
        column: pd.to_numeric(frame[field], errors="coerce").to_numpy()
        for column, field in fields.items()
    }
    return pd.DataFrame(columns, index=index).sort_index()


def _resample(frame: pd.DataFrame, frequency: str) -> pd.DataFrame:
    """Combine bars into ``frequency`` bars, labeled by when each starts."""
    combined = frame.resample(frequency, label="left", closed="left").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    )
    return combined.dropna(subset=["Close"])


async def _fmp_history(
    symbol: str,
    period: str,
    interval: str,
    api_key: str,
    today: date | None = None,
    start: date | None = None,
) -> pd.DataFrame:
    """Fetch OHLCV history from FMP: dividend-adjusted daily bars, or intraday charts.

    Bars longer than a day are built from daily bars. Intraday bars are split- but not
    dividend-adjusted, as FMP serves them.
    """
    zone = exchange_timezone(symbol)
    end = today or date.today()
    start = start or _window_start(period, end)
    window = {"symbol": symbol, "from": start.isoformat(), "to": end.isoformat()}
    if interval in INTRADAY_INTERVALS:
        chart, resample_to = INTRADAY_INTERVALS[interval]
        rows = await fmp_get(f"historical-chart/{chart}", window, api_key, None)
        fields = {"Open": "open", "High": "high", "Low": "low", "Close": "close"}
        frame = _fmp_frame(
            rows if isinstance(rows, list) else [], fields | {"Volume": "volume"}, zone
        )
        return frame if resample_to is None or frame.empty else _resample(frame, resample_to)
    rows = await fmp_get("historical-price-eod/dividend-adjusted", window, api_key, None)
    fields = {"Open": "adjOpen", "High": "adjHigh", "Low": "adjLow", "Close": "adjClose"}
    frame = _fmp_frame(rows if isinstance(rows, list) else [], fields | {"Volume": "volume"}, zone)
    if period in ("1d", "5d") and not frame.empty:
        frame = frame.tail(int(period[0]))
    if interval in LONG_INTERVALS and not frame.empty:
        frame = _resample(frame, LONG_INTERVALS[interval])
    return frame


ALPHA_VANTAGE_INTRADAY: dict[str, tuple[str, str | None]] = {
    "1m": ("1min", None),
    "2m": ("1min", "2min"),
    "5m": ("5min", None),
    "15m": ("15min", None),
    "30m": ("30min", None),
    "60m": ("60min", None),
    "1h": ("60min", None),
    "90m": ("30min", "90min"),
}
"""Alpha Vantage intraday interval for each bar size, and the size to resample to, if any."""


def _alpha_vantage_frame(
    series: dict[str, dict[str, str]], zone: str, adjust: bool
) -> pd.DataFrame:
    """Build an OHLCV frame from an Alpha Vantage time series, stamped in ``zone``.

    With ``adjust``, every price is scaled by the adjusted close over the close, as
    Alpha Vantage adjusts only the close for splits and dividends.
    """
    rows: list[tuple[pd.Timestamp, list[float | None]]] = []
    for stamp, bar in series.items():
        values = [
            alpha_vantage_number(bar.get(field))
            for field in ("1. open", "2. high", "3. low", "4. close")
        ]
        volume = alpha_vantage_number(bar.get("6. volume", bar.get("5. volume")))
        close = values[3]
        if adjust:
            adjusted = alpha_vantage_number(bar.get("5. adjusted close"))
            factor = None if adjusted is None or not close else adjusted / close
            values = [None if v is None or factor is None else v * factor for v in values]
        rows.append((pd.Timestamp(stamp), [*values, volume]))
    if not rows:
        return pd.DataFrame(columns=OHLCV_COLUMNS, index=pd.DatetimeIndex([]), dtype=float)
    index = pd.DatetimeIndex([stamp for stamp, _ in rows]).tz_localize(zone)
    frame = pd.DataFrame([values for _, values in rows], index=index, columns=OHLCV_COLUMNS)
    return frame.astype(float).sort_index()


async def _alpha_vantage_history(
    symbol: str,
    period: str,
    interval: str,
    api_key: str,
    today: date | None = None,
    start: date | None = None,
) -> pd.DataFrame:
    """Fetch OHLCV history from Alpha Vantage: adjusted daily bars, or intraday bars.

    Bars longer than a day are built from daily bars. Intraday bars cover about the last
    month, as Alpha Vantage serves them without a ``month`` parameter; they need a paid
    plan.

    Adjusted daily bars also need a paid plan. On a free key, the latest
    :data:`ALPHA_VANTAGE_FREE_DAILY_BARS` daily bars are served instead, unadjusted, and
    the frame's ``attrs`` carry a :data:`HISTORY_NOTICE` saying so, and a later provider
    with the full adjusted history is preferred (see :func:`first_success`).
    """
    end = today or date.today()
    start = pd.Timestamp(start or _window_start(period, end))
    notice: str | None = None
    if interval in ALPHA_VANTAGE_INTRADAY:
        size, resample_to = ALPHA_VANTAGE_INTRADAY[interval]
        payload = await alpha_vantage_get(
            "TIME_SERIES_INTRADAY",
            {"symbol": alpha_vantage_symbol(symbol), "interval": size, "outputsize": "full"},
            api_key,
        )
        zone = str(payload.get("Meta Data", {}).get("6. Time Zone") or DEFAULT_TIMEZONE)
        frame = _alpha_vantage_frame(payload.get(f"Time Series ({size})") or {}, zone, False)
        frame = frame[frame.index >= start.tz_localize(zone)]
        return frame if resample_to is None or frame.empty else _resample(frame, resample_to)
    try:
        payload = await alpha_vantage_get(
            "TIME_SERIES_DAILY_ADJUSTED",
            {"symbol": alpha_vantage_symbol(symbol), "outputsize": "full"},
            api_key,
        )
        adjusted = True
    except ProviderPlanError:
        payload = await alpha_vantage_get(
            "TIME_SERIES_DAILY",
            {"symbol": alpha_vantage_symbol(symbol), "outputsize": "compact"},
            api_key,
        )
        adjusted = False
        notice = (
            f"Alpha Vantage's free plan serves only the latest "
            f"{ALPHA_VANTAGE_FREE_DAILY_BARS} trading days of {symbol}, not adjusted for "
            "splits or dividends: a split shows as a price jump, and returns leave out "
            "dividends."
        )
    zone = exchange_timezone(symbol)
    frame = _alpha_vantage_frame(payload.get("Time Series (Daily)") or {}, zone, adjusted)
    frame = frame[frame.index >= start.tz_localize(zone)]
    if notice is not None:
        frame.attrs[HISTORY_NOTICE] = notice
    if period in ("1d", "5d") and not frame.empty:
        frame = frame.tail(int(period[0]))
    if interval in LONG_INTERVALS and not frame.empty:
        notice = frame.attrs.get(HISTORY_NOTICE)
        frame = _resample(frame, LONG_INTERVALS[interval])
        if notice is not None:
            frame.attrs[HISTORY_NOTICE] = notice
    return frame


TIINGO_INTRADAY: dict[str, tuple[str, str | None]] = {
    "1m": ("1min", None),
    "2m": ("1min", "2min"),
    "5m": ("5min", None),
    "15m": ("15min", None),
    "30m": ("30min", None),
    "60m": ("1hour", None),
    "1h": ("1hour", None),
    "90m": ("30min", "90min"),
}
"""Tiingo's IEX bar size for each intraday bar size, and the size to resample to, if any."""

IEX_VOLUME_NOTICE: str = (
    "Intraday bars from Tiingo's IEX feed: prices follow the whole market, but volume counts "
    "only trades on IEX, a small share of the total."
)


def _row_frame(
    rows: list[dict[str, Any]], fields: dict[str, str], date_field: str, zone: str
) -> pd.DataFrame:
    """Build an OHLCV frame from rows of a provider's JSON, stamped in ``zone``: a naive
    date is taken as ``zone``'s, and a dated time with an offset is converted to it."""
    rows = [row for row in rows if isinstance(row, dict) and row.get(date_field)]
    if not rows:
        return pd.DataFrame(columns=OHLCV_COLUMNS, index=pd.DatetimeIndex([]), dtype=float)
    frame = pd.DataFrame(rows)
    stamps = pd.DatetimeIndex(pd.to_datetime(frame[date_field], utc=False, format="mixed"))
    index = stamps.tz_localize(zone) if stamps.tz is None else stamps.tz_convert(zone)
    columns = {
        column: (
            pd.to_numeric(frame[field], errors="coerce").to_numpy()
            if field in frame
            else np.full(len(frame), np.nan)
        )
        for column, field in fields.items()
    }
    return pd.DataFrame(columns, index=index).sort_index()


async def _tiingo_history(
    symbol: str,
    period: str,
    interval: str,
    api_key: str,
    today: date | None = None,
    start: date | None = None,
) -> pd.DataFrame:
    """Fetch OHLCV history from Tiingo: split- and dividend-adjusted daily bars, back as far
    as 30 years on the free plan, or intraday bars from the IEX exchange.

    Bars longer than a day are built from daily bars. Intraday volume counts IEX trades
    alone, and the frame's ``attrs`` carry a :data:`HISTORY_NOTICE` saying so.
    """
    end = today or date.today()
    start = start or _window_start(period, end)
    ticker = tiingo_symbol(symbol)
    zone = DEFAULT_TIMEZONE
    if interval in TIINGO_INTRADAY:
        size, resample_to = TIINGO_INTRADAY[interval]
        rows = await tiingo_get(
            f"iex/{ticker}/prices",
            {
                "startDate": start.isoformat(),
                "resampleFreq": size,
                "columns": "open,high,low,close,volume",
            },
            api_key,
        )
        fields = {"Open": "open", "High": "high", "Low": "low", "Close": "close"}
        frame = _row_frame(
            rows if isinstance(rows, list) else [], fields | {"Volume": "volume"}, "date", zone
        )
        if resample_to is not None and not frame.empty:
            frame = _resample(frame, resample_to)
        frame.attrs[HISTORY_NOTICE] = IEX_VOLUME_NOTICE
        return frame
    rows = await tiingo_get(
        f"tiingo/daily/{ticker}/prices",
        {"startDate": start.isoformat(), "endDate": end.isoformat()},
        api_key,
    )
    fields = {"Open": "adjOpen", "High": "adjHigh", "Low": "adjLow", "Close": "adjClose"}
    frame = _row_frame(
        rows if isinstance(rows, list) else [], fields | {"Volume": "adjVolume"}, "date", "UTC"
    )
    # Tiingo dates its daily bars at midnight UTC; they are the exchange's calendar days.
    frame.index = pd.DatetimeIndex(frame.index).tz_localize(None).tz_localize(zone)
    if period in ("1d", "5d") and not frame.empty:
        frame = frame.tail(int(period[0]))
    if interval in LONG_INTERVALS and not frame.empty:
        frame = _resample(frame, LONG_INTERVALS[interval])
    return frame


EODHD_INTRADAY: dict[str, tuple[str, str | None]] = {
    "1m": ("1m", None),
    "2m": ("1m", "2min"),
    "5m": ("5m", None),
    "15m": ("5m", "15min"),
    "30m": ("5m", "30min"),
    "60m": ("1h", None),
    "1h": ("1h", None),
    "90m": ("5m", "90min"),
}
"""EODHD's intraday bar size for each bar size, and the size to resample to, if any."""


async def _eodhd_history(
    symbol: str,
    period: str,
    interval: str,
    api_key: str,
    today: date | None = None,
    start: date | None = None,
) -> pd.DataFrame:
    """Fetch OHLCV history from EODHD: daily bars adjusted for splits and dividends, for
    listings, ETFs, funds, and indices on exchanges worldwide, or intraday bars.

    Daily bars are scaled by EODHD's adjusted close over the close. Its free plan serves
    the last year of daily bars only; when a longer window was asked for, the frame's
    ``attrs`` carry a :data:`HISTORY_NOTICE` saying so. Intraday bars need a paid plan,
    and are not adjusted.
    """
    end = today or date.today()
    start = start or _window_start(period, end)
    code = eodhd_symbol(symbol)
    zone = exchange_timezone(symbol)
    if interval in EODHD_INTRADAY:
        size, resample_to = EODHD_INTRADAY[interval]
        first = int(pd.Timestamp(start, tz=zone).timestamp())
        rows = await eodhd_get(
            f"intraday/{code}",
            {"interval": size, "from": str(first), "to": str(int(time.time()))},
            api_key,
        )
        fields = {"Open": "open", "High": "high", "Low": "low", "Close": "close"}
        frame = _row_frame(
            rows if isinstance(rows, list) else [], fields | {"Volume": "volume"}, "datetime", "UTC"
        )
        frame.index = pd.DatetimeIndex(frame.index).tz_convert(zone)
        return frame if resample_to is None or frame.empty else _resample(frame, resample_to)
    rows = await eodhd_get(
        f"eod/{code}", {"from": start.isoformat(), "to": end.isoformat(), "period": "d"}, api_key
    )
    raw = _row_frame(
        rows if isinstance(rows, list) else [],
        {
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
            "Adjusted": "adjusted_close",
        },
        "date",
        zone,
    )
    factor = (raw["Adjusted"] / raw["Close"]).where(raw["Close"] > 0)
    frame = raw[OHLCV_COLUMNS].copy()
    for column in ("Open", "High", "Low", "Close"):
        frame[column] = raw[column] * factor
    if not frame.empty:
        first_day = pd.Timestamp(frame.index[0]).date()
        free_start = end - timedelta(days=EODHD_FREE_HISTORY_DAYS)
        if (first_day - start).days > 14 and abs((first_day - free_start).days) <= 10:
            frame.attrs[HISTORY_NOTICE] = (
                f"EODHD's free plan serves only the last year of {symbol}'s prices."
            )
    if period in ("1d", "5d") and not frame.empty:
        frame = frame.tail(int(period[0]))
    if interval in LONG_INTERVALS and not frame.empty:
        notice = frame.attrs.get(HISTORY_NOTICE)
        frame = _resample(frame, LONG_INTERVALS[interval])
        if notice is not None:
            frame.attrs[HISTORY_NOTICE] = notice
    return frame


FINNHUB_RESOLUTIONS: dict[str, tuple[str, str | None]] = {
    "1m": ("1", None),
    "2m": ("1", "2min"),
    "5m": ("5", None),
    "15m": ("15", None),
    "30m": ("30", None),
    "60m": ("60", None),
    "1h": ("60", None),
    "90m": ("30", "90min"),
}
"""Finnhub's candle resolution for each intraday bar size, and the size to resample to."""


async def _finnhub_history(
    symbol: str,
    period: str,
    interval: str,
    api_key: str,
    today: date | None = None,
    start: date | None = None,
) -> pd.DataFrame:
    """Fetch OHLCV candles from Finnhub, which a paid plan serves; its free plan refuses
    them, and the refusal is remembered. Candles are adjusted for splits but not for
    dividends, and the frame's ``attrs`` carry a :data:`HISTORY_NOTICE` saying so, so a
    provider with adjusted bars is preferred."""
    end = today or date.today()
    start = start or _window_start(period, end)
    zone = exchange_timezone(symbol)
    resolution, resample_to = FINNHUB_RESOLUTIONS.get(interval, ("D", None))
    payload = await finnhub_get(
        "stock/candle",
        {
            "symbol": finnhub_symbol(symbol),
            "resolution": resolution,
            "from": str(int(pd.Timestamp(start, tz=zone).timestamp())),
            "to": str(int(time.time())),
        },
        api_key,
        None,
    )
    if not isinstance(payload, dict) or payload.get("s") != "ok":
        return pd.DataFrame(columns=OHLCV_COLUMNS, index=pd.DatetimeIndex([]), dtype=float)
    index = pd.DatetimeIndex(pd.to_datetime(payload.get("t") or [], unit="s", utc=True))
    frame = pd.DataFrame(
        {
            column: pd.to_numeric(pd.Series(payload.get(field) or []), errors="coerce").to_numpy()
            for column, field in zip(OHLCV_COLUMNS, ("o", "h", "l", "c", "v"), strict=True)
        },
        index=index.tz_convert(zone),
    ).sort_index()
    if resolution == "D":
        frame.index = pd.DatetimeIndex(frame.index).normalize()
        if interval in LONG_INTERVALS and not frame.empty:
            frame = _resample(frame, LONG_INTERVALS[interval])
    elif resample_to is not None and not frame.empty:
        frame = _resample(frame, resample_to)
    frame.attrs[HISTORY_NOTICE] = (
        "Finnhub's candles are adjusted for splits but not for dividends, so returns leave "
        "dividends out."
    )
    return frame


def _clean_history(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep OHLCV columns, sort by date, and drop rows missing a close price."""
    missing = [column for column in OHLCV_COLUMNS if column not in frame.columns]
    if missing:
        raise DataFetchError(f"History is missing columns: {missing}.")
    cleaned = frame.loc[:, OHLCV_COLUMNS].sort_index()
    cleaned = cleaned[~cleaned.index.duplicated(keep="last")].dropna(subset=["Close"])
    cleaned.attrs = dict(frame.attrs)
    return cleaned


def _history_to_json(frame: pd.DataFrame) -> str:
    """Serialize a cleaned history frame, keeping its index's time zone."""
    index = pd.DatetimeIndex(frame.index)
    return json.dumps(
        {
            "tz": None if index.tz is None else str(index.tz),
            "name": index.name,
            "index": [timestamp.isoformat() for timestamp in index],
            "columns": {column: frame[column].tolist() for column in frame.columns},
            "notice": frame.attrs.get(HISTORY_NOTICE),
        }
    )


def _history_from_json(text: str) -> pd.DataFrame:
    """Rebuild a history frame serialized by :func:`_history_to_json`."""
    payload: dict[str, Any] = json.loads(text)
    tz: str | None = payload["tz"]
    if tz is None:
        index = pd.DatetimeIndex(pd.to_datetime(payload["index"]))
    else:
        index = pd.DatetimeIndex(pd.to_datetime(payload["index"], utc=True)).tz_convert(tz)
    index.name = payload["name"]
    frame = pd.DataFrame(payload["columns"], index=index, columns=OHLCV_COLUMNS)
    if payload.get("notice"):
        frame.attrs[HISTORY_NOTICE] = payload["notice"]
    return frame


def _history_ttl(interval: str) -> float:
    """Return how long bars of ``interval`` stay cached: minutes and hours expire sooner."""
    intraday = interval.endswith(("m", "h")) and not interval.endswith("mo")
    return INTRADAY_HISTORY_TTL_SECONDS if intraday else HISTORY_TTL_SECONDS


HISTORY_SHORTFALL: str = "shortfall"
"""Key in a history frame's ``attrs`` for the :class:`Shortfall` list saying why a fuller
history was not served: which keys were out of requests, refused, or failed."""
HISTORY_STALE: str = "stale"
"""Key in a history frame's ``attrs``, true when the bars are a stored copy that ends
before the latest close because no key could fetch the days since."""

STORE_PERIOD: str = "10y"
"""The least history fetched for the store: one request brings ten years, so later pages
and backtests that ask for less, or for as much, reuse it."""
STORE_INTERVALS: frozenset[str] = frozenset({"1d", *LONG_INTERVALS})
"""Bar sizes served from stored daily bars."""
STORE_OVERLAP_DAYS: int = 10
"""Days before a stored history's last bar fetched again with the new days, to tell
whether a dividend or split has since changed the adjusted prices."""
STORE_TAIL_MAX_DAYS: int = 300
"""A stored history older than this is fetched again in full rather than extended."""
STORE_MEMORY_SECONDS: float = 120.0
"""How long a stored history read from the database is reused from memory, so one page's
several requests for the same symbol read it once."""
DAILY_BAR_READY_HOUR: int = 18
"""Local hour at the exchange after which a trading day's bar is expected from providers."""
SPLIT_RATIO: float = 1.2
"""A change of adjusted prices beyond this factor is taken as a split, which also scales
volumes."""


@dataclass(frozen=True)
class Shortfall:
    """Why one provider did not serve a fuller history."""

    provider: Provider
    reason: Literal["limit", "plan", "failed"]
    message: str
    resets_at: datetime | None = None


_store_memory: "OrderedDict[tuple[str, str], tuple[float, dict[str, StoredHistory]]]" = (
    OrderedDict()
)
"""(user, symbol) -> (when read, the stored histories by provider)."""
_store_locks: dict[tuple[str, str], asyncio.Lock] = {}
"""(user, symbol) -> the lock that makes concurrent requests for it share one fetch."""


def forget_stored_prices(user_id: str) -> None:
    """Drop the user's stored prices kept in memory, as after they delete them."""
    for entry in [entry for entry in _store_memory if entry[0] == user_id]:
        del _store_memory[entry]


def _pack(frame: pd.DataFrame) -> bytes:
    """A history frame as compressed JSON, for the store."""
    return zlib.compress(_history_to_json(frame).encode(), 6)


def _unpack(data: bytes) -> pd.DataFrame:
    """A history frame from the store."""
    return _history_from_json(zlib.decompress(data).decode())


def _daily_ready(symbol: str, now: datetime) -> datetime:
    """When the latest daily bar of ``symbol`` was due: 6 PM at its exchange on the latest
    weekday at or before ``now``. A history fetched since is fresh."""
    zone = ZoneInfo(exchange_timezone(symbol))
    local = now.astimezone(zone)
    ready = local.replace(hour=DAILY_BAR_READY_HOUR, minute=0, second=0, microsecond=0)
    if local < ready:
        ready -= timedelta(days=1)
    while ready.weekday() >= 5:
        ready -= timedelta(days=1)
    return ready


def _covers(stored: StoredHistory, start: date) -> bool:
    """Whether ``stored`` reaches back to ``start``: its bars begin by then, or it was asked
    for from then and, with no notice of a short plan, the listing simply began later."""
    return stored.first_day <= start + timedelta(days=14) or (
        stored.notice is None and stored.requested_from <= start
    )


def _store_start(period: str, today: date) -> date:
    """The first day fetched for the store: ``period``'s, or ten years back if that is more."""
    return min(_window_start(period, today), _window_start(STORE_PERIOD, today))


def _shape(frame: pd.DataFrame, period: str, interval: str, today: date) -> pd.DataFrame:
    """Stored daily bars cut to ``period`` and built into ``interval`` bars, keeping the
    frame's notes."""
    attrs = dict(frame.attrs)
    index = pd.DatetimeIndex(frame.index)
    start = pd.Timestamp(_window_start(period, today))
    if index.tz is not None:
        start = start.tz_localize(index.tz)
    shaped = frame[index >= start]
    if period in ("1d", "5d") and not shaped.empty:
        shaped = shaped.tail(int(period[0]))
    if interval in LONG_INTERVALS and not shaped.empty:
        shaped = _resample(shaped, LONG_INTERVALS[interval])
    shaped = shaped.copy()
    shaped.attrs = attrs
    return shaped


def _merge(old: pd.DataFrame, tail: pd.DataFrame) -> pd.DataFrame | None:
    """``old`` bars extended by ``tail``, the bars of the last days fetched again.

    Adjusted prices are scaled back from the present, so a dividend or split since ``old``
    was fetched changes all of its prices by one factor: the ratio of ``tail``'s close to
    ``old``'s on their first common day, applied to the bars before ``tail``. ``None`` when
    they share no day, and the whole history must be fetched again.
    """
    if tail.empty:
        return old
    common = pd.DatetimeIndex(tail.index).intersection(pd.DatetimeIndex(old.index))
    if common.empty:
        return None
    first = common[0]
    before = _finite(old["Close"].loc[first]) or 0.0
    after = _finite(tail["Close"].loc[first]) or 0.0
    if not (math.isfinite(before) and math.isfinite(after)) or before <= 0 or after <= 0:
        return None
    ratio = after / before
    head = old[pd.DatetimeIndex(old.index) < pd.DatetimeIndex(tail.index)[0]].copy()
    if abs(ratio - 1.0) > 1e-9:
        for column in ("Open", "High", "Low", "Close"):
            head[column] = head[column] * ratio
        if ratio > SPLIT_RATIO or ratio < 1 / SPLIT_RATIO:
            head["Volume"] = head["Volume"] / ratio
    merged = pd.concat([head, tail[OHLCV_COLUMNS]])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    merged.attrs = dict(old.attrs)
    return merged


async def _load_stored(
    user_id: str, symbol: str, providers: Sequence[str]
) -> dict[str, StoredHistory]:
    """The user's stored histories of ``symbol`` from ``providers``, from memory if read in
    the last :data:`STORE_MEMORY_SECONDS`; none if the database cannot be read."""
    entry = (user_id, symbol)
    remembered = _store_memory.get(entry)
    if remembered is not None and time.monotonic() - remembered[0] < STORE_MEMORY_SECONDS:
        return {p: h for p, h in remembered[1].items() if p in providers}
    try:
        async with get_sessionmaker()() as session:
            found = await price_store.load_histories(session, user_id, symbol, providers)
    except SQLAlchemyError as exc:
        logger.warning("Could not read stored prices of %s: %s", symbol, exc)
        return {}
    _store_memory[entry] = (time.monotonic(), dict(found))
    _store_memory.move_to_end(entry)
    while len(_store_memory) > RECALL_MEMORY_SIZE:
        _store_memory.popitem(last=False)
    return found


async def _save_stored(
    user_id: str, provider: Provider, symbol: str, frame: pd.DataFrame, requested_from: date
) -> None:
    """Store ``frame`` as ``provider``'s history of ``symbol`` for the user."""
    index = pd.DatetimeIndex(frame.index)
    history = StoredHistory(
        provider=provider,
        symbol=symbol,
        requested_from=requested_from,
        first_day=pd.Timestamp(index[0]).date(),
        last_day=pd.Timestamp(index[-1]).date(),
        bars=len(frame),
        notice=frame.attrs.get(HISTORY_NOTICE),
        fetched_at=datetime.now(timezone.utc),
        data=_pack(frame),
    )
    remembered = _store_memory.get((user_id, symbol))
    if remembered is not None:
        remembered[1][provider] = history
    try:
        async with get_sessionmaker()() as session:
            await price_store.save_history(session, user_id, history)
    except SQLAlchemyError as exc:
        logger.warning("Could not store the prices of %s: %s", symbol, exc)


async def _note_read(user_id: str, provider: str, symbol: str) -> None:
    try:
        async with get_sessionmaker()() as session:
            await price_store.mark_read(session, user_id, provider, symbol)
    except SQLAlchemyError as exc:
        logger.warning("Could not note a read of %s's stored prices: %s", symbol, exc)


def _history_reader(provider: Provider) -> Callable[..., Awaitable[pd.DataFrame]]:
    """``provider``'s bar reader, looked up when called so tests can replace it."""
    readers: dict[Provider, Callable[..., Awaitable[pd.DataFrame]]] = {
        "tiingo": _tiingo_history,
        "fmp": _fmp_history,
        "eodhd": _eodhd_history,
        "alpha_vantage": _alpha_vantage_history,
        "finnhub": _finnhub_history,
    }
    return readers[provider]


def _shortfalls(passed: list[tuple[Provider, Exception]]) -> list[Shortfall]:
    """Why each provider passed over did not serve the history."""
    found: list[Shortfall] = []
    for provider, error in passed:
        if isinstance(error, SymbolNotFoundError):
            continue
        if isinstance(error, ProviderLimitError):
            found.append(Shortfall(provider, "limit", str(error), error.resets_at))
        elif isinstance(error, ProviderPlanError):
            found.append(Shortfall(provider, "plan", str(error)))
        else:
            found.append(Shortfall(provider, "failed", str(error)))
    return found


def _when(moment: datetime | None, symbol: str) -> str:
    """A reset time for people, in US Eastern time: "3:00 PM ET today" or "Nov 6, 12:00 AM ET"."""
    if moment is None:
        return ""
    local = moment.astimezone(ZoneInfo(DEFAULT_TIMEZONE))
    clock = local.strftime("%I:%M %p").lstrip("0")
    today = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date()
    if local.date() == today:
        return f"{clock} ET today"
    if local.date() == today + timedelta(days=1):
        return f"{clock} ET tomorrow"
    return f"{local.strftime('%b')} {local.day}, {clock} ET"


def shortfall_text(shortfalls: list[Shortfall], symbol: str) -> str:
    """The shortfalls in plain sentences: which key ran out or refused, and when it resets."""
    parts: list[str] = []
    for item in shortfalls:
        name = PROVIDER_NAMES[item.provider]
        if item.reason == "limit":
            when = _when(item.resets_at, symbol)
            parts.append(
                f"your {name} key has used its plan's requests for now"
                + (f"; PyQxel will ask it again at {when}" if when else "")
            )
        elif item.reason == "plan":
            parts.append(f"your {name} plan does not cover {symbol}'s history")
        else:
            parts.append(f"{name} could not be reached")
    if not parts:
        return ""
    joined = "; ".join(parts)
    return joined[0].upper() + joined[1:] + "."


async def _fetch_stored_daily(
    symbol: str, period: str, interval: str, user_id: str
) -> pd.DataFrame | None:
    """Daily (or longer) bars for the user, through their stored price history.

    A fresh stored history that reaches back far enough is served without a request. A
    stale one is extended by the days since, and a missing one is fetched ten years back
    (or further, for ``max``) so later requests reuse it. When no key can do better than
    a free plan's short history, a longer stored copy is served instead, saying how old
    it is. A frame that is still short carries :data:`HISTORY_SHORTFALL`.

    Returns:
        The bars, or ``None`` when every provider answered with none.
    """
    today = date.today()
    now = datetime.now(timezone.utc)
    start = _window_start(period, today)
    fetch_from = _store_start(period, today)
    providers = available_providers("history", symbol)
    licensed = [p for p, _ in providers if p != "yfinance"]
    if len(_store_locks) > RECALL_MEMORY_SIZE:
        for entry in [e for e, held in _store_locks.items() if not held.locked()]:
            del _store_locks[entry]
    lock = _store_locks.setdefault((user_id, symbol), asyncio.Lock())
    async with lock:
        stored = await _load_stored(user_id, symbol, licensed)
        ready = _daily_ready(symbol, now)
        for provider in licensed:
            kept = stored.get(provider)
            if kept and kept.notice is None and kept.fetched_at >= ready and _covers(kept, start):
                await _note_read(user_id, provider, symbol)
                return _shape(_unpack(kept.data), period, interval, today)

        def attempt(
            provider: Provider, api_key: str | None
        ) -> Callable[[], Awaitable[pd.DataFrame | None]]:
            async def run() -> pd.DataFrame | None:
                if provider == "yfinance":
                    raw = await asyncio.to_thread(_yfinance_history, symbol, period, "1d")
                    return None if raw.empty else _clean_history(raw)
                assert api_key is not None
                read = _history_reader(provider)
                kept = stored.get(provider)
                if (
                    kept is not None
                    and kept.fetched_at >= ready
                    and (kept.notice is not None or _covers(kept, start))
                ):
                    # Fetching again today would bring nothing more.
                    return _unpack(kept.data)
                try:
                    if (
                        kept is not None
                        and _covers(kept, start)
                        and (today - kept.last_day).days <= STORE_TAIL_MAX_DAYS
                    ):
                        tail_from = kept.last_day - timedelta(days=STORE_OVERLAP_DAYS)
                        raw = await read(symbol, "1y", "1d", api_key, start=tail_from)
                        tail = _clean_history(raw) if not raw.empty else raw
                        merged = _merge(_unpack(kept.data), tail)
                        if merged is not None:
                            await _save_stored(
                                user_id, provider, symbol, merged, kept.requested_from
                            )
                            return merged
                    raw = await read(symbol, "max", "1d", api_key, start=fetch_from)
                except DataFetchError:
                    raise
                except Exception as exc:
                    raise DataFetchError(f"{provider} history failed: {exc}") from exc
                if raw.empty:
                    return None
                frame = _clean_history(raw)
                if not frame.empty:
                    await _save_stored(user_id, provider, symbol, frame, fetch_from)
                return frame

            return run

        def short(found: pd.DataFrame | None) -> bool:
            return found is not None and HISTORY_NOTICE in found.attrs

        passed: list[tuple[Provider, Exception]] = []
        failure: DataFetchError | None = None
        frame: pd.DataFrame | None
        try:
            frame = await first_success(
                symbol,
                "price history",
                [(p, attempt(p, k)) for p, k in providers],
                partial=short,
                passed=passed,
                no_better=no_longer_history(dict(providers)),
            )
        except DataFetchError as exc:
            frame, failure = None, exc
        if frame is None or short(frame):
            fallback = _longest_stored(stored, licensed, start)
            if fallback is not None and (
                frame is None
                or fallback.first_day
                < pd.Timestamp(pd.DatetimeIndex(frame.index)[0]).date() - timedelta(days=14)
            ):
                frame = _unpack(fallback.data)
                reasons = shortfall_text(_shortfalls(passed), symbol)
                frame.attrs[HISTORY_STALE] = True
                frame.attrs[HISTORY_NOTICE] = (
                    f"These prices of {symbol} end {fallback.last_day:%b} "
                    f"{fallback.last_day.day}: they are the copy PyQxel kept from "
                    f"{PROVIDER_NAMES.get(fallback.provider, fallback.provider)}, since "
                    "no key could fetch "
                    "the days since." + (f" {reasons}" if reasons else "")
                )
                await _note_read(user_id, fallback.provider, symbol)
        if frame is None:
            if failure is not None:
                raise failure
            return None
        if short(frame) and not frame.attrs.get(HISTORY_STALE):
            frame.attrs[HISTORY_SHORTFALL] = _shortfalls(passed)
        return _shape(frame, period, interval, today)


def _longest_stored(
    stored: dict[str, StoredHistory], providers: Sequence[str], start: date
) -> StoredHistory | None:
    """The stored history, among ``providers``', that reaches back to ``start`` and ends
    latest; ``None`` if none reaches back that far."""
    covering = [stored[p] for p in providers if p in stored and _covers(stored[p], start)]
    return max(covering, key=lambda history: history.last_day, default=None)


async def fetch_price_history(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
) -> pd.DataFrame:
    """Fetch split- and dividend-adjusted OHLCV history for ``symbol``.

    Args:
        symbol: Ticker symbol, e.g. ``"AAPL"``.
        period: Lookback period (``"1mo"``, ``"1y"``, ``"max"``, ...).
        interval: Bar size (``"1d"``, ``"1wk"``, ``"1h"``, ...).

    Returns:
        A frame indexed by bar time in the exchange's time zone, with ``Open``, ``High``,
        ``Low``, ``Close``, and ``Volume`` columns, sorted ascending, de-duplicated, and
        without rows lacking a close. It is empty when ``symbol`` exists but has no bars
        in the window. When the provider could serve only part of the window, or bars
        that are not adjusted, ``attrs[HISTORY_NOTICE]`` says so.

    Raises:
        ValueError: If ``symbol`` is empty.
        ProviderNotConfiguredError: If the user has no market data key and the fallback
            is off.
        ProviderPlanError: If the user's FMP plan does not cover the request.
        SymbolNotFoundError: If no bars are returned and ``symbol`` is unknown.
        DataFetchError: If every provider fails, or the symbol's existence cannot be
            checked.
    """
    symbol = _normalize_symbol(symbol)
    keys = current_provider_keys()
    if interval in STORE_INTERVALS and keys.user_id is not None and keys.store_history:
        try:
            stored = await _fetch_stored_daily(symbol, period, interval, keys.user_id)
        except ProviderPlanError as exc:
            hint = await plan_hint("history", symbol)
            if not hint:
                raise
            raise ProviderPlanError(f"{exc} {hint}") from exc
        if stored is None:
            await fetch_ticker_info(symbol)
            return pd.DataFrame(columns=OHLCV_COLUMNS, index=pd.DatetimeIndex([]), dtype=float)
        return stored

    def attempt(
        provider: Provider, api_key: str | None
    ) -> Callable[[], Awaitable[pd.DataFrame | None]]:
        async def run() -> pd.DataFrame | None:
            key = f"history:{provider}:{symbol}:{period}:{interval}"
            cached = await cache_get(key)
            if cached is not None:
                try:
                    return _history_from_json(cached)
                except ValueError, KeyError, TypeError:
                    logger.warning("Ignoring unreadable cached history for %s.", symbol)
            try:
                if provider == "yfinance":
                    raw = await asyncio.to_thread(_yfinance_history, symbol, period, interval)
                else:
                    assert api_key is not None
                    if provider == "tiingo":
                        read = _tiingo_history
                    elif provider == "fmp":
                        read = _fmp_history
                    elif provider == "eodhd":
                        read = _eodhd_history
                    elif provider == "alpha_vantage":
                        read = _alpha_vantage_history
                    else:
                        read = _finnhub_history
                    raw = await read(symbol, period, interval, api_key)
            except DataFetchError:
                raise
            except Exception as exc:
                raise DataFetchError(f"{provider} history failed: {exc}") from exc
            if raw.empty:
                return None
            frame = _clean_history(raw)
            if not frame.empty:
                await cache_set(key, _history_to_json(frame), _history_ttl(interval))
            return frame

        return run

    kind: DataKind = "intraday" if interval in INTRADAY_INTERVALS else "history"
    try:
        providers = available_providers(kind, symbol)
        frame = await first_success(
            symbol,
            "price history",
            [(p, attempt(p, k)) for p, k in providers],
            # A free plan's short or unadjusted history is used only if no key does better.
            partial=lambda found: found is not None and HISTORY_NOTICE in found.attrs,
            no_better=None if kind == "intraday" else no_longer_history(dict(providers)),
        )
    except ProviderPlanError as exc:
        hint = await plan_hint(kind, symbol)
        if not hint:
            raise
        raise ProviderPlanError(f"{exc} {hint}") from exc
    if frame is None:
        # Providers return the same empty answer for an unknown symbol and for a real
        # one with no bars in the window, so ask for a snapshot to tell the two apart.
        await fetch_ticker_info(symbol)
        return pd.DataFrame(columns=OHLCV_COLUMNS, index=pd.DatetimeIndex([]), dtype=float)
    return frame


def _finite(value: object) -> float | None:
    """Return ``value`` as a float, mapping missing, NaN, and infinite values to ``None``."""
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) else None


def _build_quote(
    symbol: str,
    price: float | None,
    previous_close: float | None,
    source: str,
    **fields: Any,
) -> Quote:
    """Assemble a :class:`Quote`, deriving the change from the price and previous close."""
    if price is None:
        raise SymbolNotFoundError(f"{source} has no price for {symbol!r}.")
    if previous_close is not None and previous_close <= 0:
        previous_close = None
    change = None if previous_close is None else price - previous_close
    volume = fields.pop("volume", None)
    return Quote(
        symbol=symbol,
        price=price,
        previous_close=previous_close,
        change=change,
        change_percent=(
            None if change is None or previous_close is None else change / previous_close
        ),
        volume=None if volume is None else round(volume),
        as_of=datetime.now(timezone.utc),
        source=source,
        **fields,
    )


def _yfinance_quote(symbol: str) -> Quote:
    """Fetch the latest quote from yfinance's lightweight ``fast_info`` (blocking)."""
    info = yf.Ticker(symbol).fast_info
    return _build_quote(
        symbol,
        price=_finite(info.last_price),
        previous_close=_finite(info.previous_close),
        source="yfinance",
        day_high=_finite(info.day_high),
        day_low=_finite(info.day_low),
        volume=_finite(info.last_volume),
        currency=info.currency,
    )


async def _fmp_quote(symbol: str, api_key: str, client: httpx2.AsyncClient | None) -> Quote:
    """Fetch the latest quote from the FMP quote endpoint."""
    payload = await fmp_get("quote", {"symbol": symbol}, api_key, client)
    if not isinstance(payload, list) or not payload:
        raise SymbolNotFoundError(f"FMP has no quote for {symbol!r}.")
    quote: dict[str, Any] = payload[0]
    return _build_quote(
        symbol,
        price=_finite(quote.get("price")),
        previous_close=_finite(quote.get("previousClose")),
        source="fmp",
        day_high=_finite(quote.get("dayHigh")),
        day_low=_finite(quote.get("dayLow")),
        volume=_finite(quote.get("volume")),
    )


async def _finnhub_quote(symbol: str, api_key: str, client: httpx2.AsyncClient | None) -> Quote:
    """Fetch the latest quote from Finnhub, real time for US listings on its free plan."""
    payload = await finnhub_get("quote", {"symbol": finnhub_symbol(symbol)}, api_key, client)
    if not isinstance(payload, dict):
        raise DataFetchError("Finnhub returned an unexpected quote.")
    # Finnhub answers an unknown symbol with zeros rather than an error.
    if not payload.get("t"):
        raise SymbolNotFoundError(f"Finnhub has no quote for {symbol!r}.")
    return _build_quote(
        symbol,
        price=_positive(payload.get("c")),
        previous_close=_positive(payload.get("pc")),
        source="finnhub",
        day_high=_positive(payload.get("h")),
        day_low=_positive(payload.get("l")),
    )


async def _alpha_vantage_quote(
    symbol: str, api_key: str, client: httpx2.AsyncClient | None
) -> Quote:
    """Fetch the latest quote from Alpha Vantage's global quote."""
    payload = await alpha_vantage_get(
        "GLOBAL_QUOTE", {"symbol": alpha_vantage_symbol(symbol)}, api_key, client
    )
    quote: dict[str, Any] = payload.get("Global Quote") or {}
    if not quote:
        raise SymbolNotFoundError(f"Alpha Vantage has no quote for {symbol!r}.")
    return _build_quote(
        symbol,
        price=alpha_vantage_number(quote.get("05. price")),
        previous_close=alpha_vantage_number(quote.get("08. previous close")),
        source="alpha_vantage",
        day_high=alpha_vantage_number(quote.get("03. high")),
        day_low=alpha_vantage_number(quote.get("04. low")),
        volume=alpha_vantage_number(quote.get("06. volume")),
    )


async def _tiingo_quote(symbol: str, api_key: str, client: httpx2.AsyncClient | None) -> Quote:
    """Fetch the latest price of a US listing from Tiingo: its own last price, built from
    the whole market, which its free plan serves."""
    rows = await tiingo_get("iex/", {"tickers": tiingo_symbol(symbol)}, api_key, client)
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise SymbolNotFoundError(f"Tiingo has no quote for {symbol!r}.")
    quote: dict[str, Any] = rows[0]
    return _build_quote(
        symbol,
        price=_positive(_finite(quote.get("tngoLast") or quote.get("last"))),
        previous_close=_positive(_finite(quote.get("prevClose"))),
        source="tiingo",
        day_high=_positive(_finite(quote.get("high"))),
        day_low=_positive(_finite(quote.get("low"))),
    )


async def _eodhd_quote(symbol: str, api_key: str, client: httpx2.AsyncClient | None) -> Quote:
    """Fetch the latest price from EODHD, delayed 15 to 20 minutes, for listings, funds, and
    indices worldwide."""
    quote = await eodhd_get(f"real-time/{eodhd_symbol(symbol)}", {}, api_key, client)
    if not isinstance(quote, dict):
        raise SymbolNotFoundError(f"EODHD has no quote for {symbol!r}.")
    return _build_quote(
        symbol,
        price=_positive(_finite(quote.get("close"))),
        previous_close=_positive(_finite(quote.get("previousClose"))),
        source="eodhd",
        day_high=_positive(_finite(quote.get("high"))),
        day_low=_positive(_finite(quote.get("low"))),
        volume=_finite(quote.get("volume")),
    )


async def fetch_quote(symbol: str, client: httpx2.AsyncClient | None = None) -> Quote:
    """Fetch the latest price of ``symbol`` and its change since the previous close.

    Quotes are cached for :data:`QUOTE_TTL_SECONDS` per provider, so frequent pollers do
    not each hit the provider.

    Args:
        symbol: Ticker symbol, e.g. ``"AAPL"``. Case and surrounding whitespace are ignored.
        client: Optional shared HTTP client for FMP.

    Returns:
        The latest quote.

    Raises:
        ValueError: If ``symbol`` is empty.
        ProviderNotConfiguredError: If the user has no market data key and the fallback
            is off.
        ProviderPlanError: If the user's plan with its only provider does not cover
            ``symbol``.
        SymbolNotFoundError: If the providers that answered have no price for ``symbol``.
        DataFetchError: If every provider fails.
    """
    symbol = _normalize_symbol(symbol)

    def attempt(provider: Provider, api_key: str | None) -> Callable[[], Awaitable[Quote]]:
        async def run() -> Quote:
            cached = _quote_cache.get((provider, symbol))
            if cached is not None and time.monotonic() - cached[0] < QUOTE_TTL_SECONDS:
                return cached[1]
            if provider == "fmp":
                assert api_key is not None
                try:
                    quote = await _fmp_quote(symbol, api_key, client)
                except ProviderPlanError as exc:
                    # FMP answers quotes for unknown symbols with a plan error; its
                    # profile tells an unknown symbol apart from an excluded one, and an
                    # unknown symbol's refusal is not kept.
                    if not exc.remembered:
                        try:
                            await _fmp_info(symbol, api_key, client)
                        except SymbolNotFoundError:
                            await forget_fmp_refusal("quote", symbol, api_key)
                            raise
                    raise
            elif provider == "finnhub":
                assert api_key is not None
                quote = await _finnhub_quote(symbol, api_key, client)
            elif provider == "alpha_vantage":
                assert api_key is not None
                quote = await _alpha_vantage_quote(symbol, api_key, client)
            elif provider == "tiingo":
                assert api_key is not None
                quote = await _tiingo_quote(symbol, api_key, client)
            elif provider == "eodhd":
                assert api_key is not None
                quote = await _eodhd_quote(symbol, api_key, client)
            else:
                try:
                    quote = await asyncio.to_thread(_yfinance_quote, symbol)
                except SymbolNotFoundError:
                    raise
                except Exception as exc:
                    # fast_info fails alike for unknown symbols and outages; the snapshot
                    # tells them apart.
                    await asyncio.to_thread(_yfinance_info, symbol)
                    raise DataFetchError(f"yfinance quote failed: {exc}") from exc
            _quote_cache[(provider, symbol)] = (time.monotonic(), quote)
            return quote

        return run

    return await first_success(
        symbol, "a quote", [(p, attempt(p, k)) for p, k in available_providers("quote", symbol)]
    )


def clear_quote_cache() -> None:
    """Forget cached quotes."""
    _quote_cache.clear()
