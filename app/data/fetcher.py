"""Async market data fetchers.

Financial Modeling Prep (FMP), a licensed provider, is the primary source, called with
the requesting user's own API key (see :mod:`app.core.credentials`), so each user's
data is billed to their own plan. Alpha Vantage, also licensed and also on the user's
own key, comes next: it answers when FMP fails or the user's FMP plan does not cover a
request, and fills in ticker info fields FMP leaves empty. yfinance, which scrapes
Yahoo, is used only when ``YFINANCE_FALLBACK`` is on, for development, after the
licensed providers. A user with no usable key gets
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
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from typing import Any, Literal, TypeVar

import httpx2
import pandas as pd
import yfinance as yf

from app.core.cache import cache_get, cache_set
from app.core.config import get_settings
from app.core.credentials import current_provider_keys
from app.models.quote import Quote
from app.models.stock import TickerInfo

logger = logging.getLogger(__name__)

FMP_BASE_URL: str = "https://financialmodelingprep.com/stable"
ALPHA_VANTAGE_URL: str = "https://www.alphavantage.co/query"
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
}
"""How long each Alpha Vantage answer is reused, unknown symbols included. Its free plan
allows 25 requests a day, so even quotes are kept a minute."""
ALPHA_VANTAGE_MEMORY_SIZE: int = 512
"""Alpha Vantage answers kept in process memory, for deployments without Redis."""
ALPHA_VANTAGE_PLAN_TTL_SECONDS: float = 24 * 60 * 60
"""How long a key's plan is taken not to cover a premium function before asking again."""
ALPHA_VANTAGE_FREE_FUNCTIONS: frozenset[str] = frozenset(
    {"GLOBAL_QUOTE", "SYMBOL_SEARCH", "TIME_SERIES_DAILY", "INCOME_STATEMENT",
     "BALANCE_SHEET", "CASH_FLOW", "CURRENCY_EXCHANGE_RATE"}
)  # fmt: skip
"""Functions a free key may call, as checked against a free key in October 2026; the
company overview, adjusted daily bars and every intraday series need a paid plan.
Alpha Vantage answers an invalid key as if it asked for a premium function, so a premium
refusal of one of these means a bad key."""
ALPHA_VANTAGE_SPACING_SECONDS: float = 1.1
"""Least time between Alpha Vantage requests from this process: free keys refuse bursts
of more than one request a second."""
ALPHA_VANTAGE_FREE_DAILY_BARS: int = 100
"""Daily bars Alpha Vantage's free plan serves, the latest ones, unadjusted."""
HISTORY_NOTICE: str = "notice"
"""Key in a history frame's ``attrs`` for a provider's note on what the bars lack."""

Provider = Literal["fmp", "alpha_vantage", "yfinance"]

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

T = TypeVar("T")


class DataFetchError(RuntimeError):
    """Raised when market data cannot be retrieved from any provider."""


class SymbolNotFoundError(DataFetchError):
    """Raised when a provider answers successfully but has no data for the symbol."""


class ProviderNotConfiguredError(DataFetchError):
    """Raised when the user has no usable FMP key and the yfinance fallback is off."""


class ProviderPlanError(DataFetchError):
    """Raised when the user's FMP plan does not cover the request."""


class AlphaVantageBurstError(DataFetchError):
    """Raised when Alpha Vantage refuses requests sent less than a second apart."""


def _normalize_symbol(symbol: str) -> str:
    """Return ``symbol`` stripped and upper-cased, rejecting empty input."""
    cleaned = symbol.strip().upper()
    if not cleaned:
        raise ValueError("Ticker symbol must not be empty.")
    return cleaned


def available_providers() -> list[tuple[Provider, str | None]]:
    """The providers to try for the current user, in order, with the key to use.

    Raises:
        ProviderNotConfiguredError: If the user has neither an FMP nor an Alpha Vantage
            key and the fallback is off.
    """
    keys = current_provider_keys()
    providers: list[tuple[Provider, str | None]] = []
    if keys.fmp is not None:
        providers.append(("fmp", keys.fmp.get_secret_value()))
    if keys.alpha_vantage is not None:
        providers.append(("alpha_vantage", keys.alpha_vantage.get_secret_value()))
    if get_settings().yfinance_fallback:
        providers.append(("yfinance", None))
    if not providers:
        raise ProviderNotConfiguredError(
            "Market data is billed to your own Financial Modeling Prep or Alpha Vantage "
            "plan: add your API key with PUT /api/v1/me/credentials."
        )
    return providers


def alpha_vantage_key() -> str | None:
    """The current user's Alpha Vantage key, if they stored one."""
    key = current_provider_keys().alpha_vantage
    return None if key is None else key.get_secret_value()


async def first_success(
    symbol: str,
    what: str,
    attempts: list[tuple[Provider, Callable[[], Awaitable[T]]]],
) -> T:
    """Return the first provider's answer, trying the next when one fails.

    Raises:
        SymbolNotFoundError: If every provider answered that it has no such symbol.
        DataFetchError: The only provider's own error, such as
            :class:`ProviderPlanError`, or a general one when several failed.
    """
    errors: list[Exception] = []
    for provider, attempt in attempts:
        try:
            return await attempt()
        except Exception as exc:
            errors.append(exc)
            logger.warning("%s %s lookup failed for %s: %s", provider, what, symbol, exc)
    if errors and all(isinstance(error, SymbolNotFoundError) for error in errors):
        raise SymbolNotFoundError(f"Unknown ticker symbol {symbol!r}.") from errors[-1]
    if len(errors) == 1 and isinstance(errors[0], DataFetchError):
        raise errors[0]
    raise DataFetchError(f"Could not fetch {what} for {symbol!r}.") from errors[-1]


async def fmp_get(
    path: str, params: dict[str, str], api_key: str, client: httpx2.AsyncClient | None
) -> Any:
    """GET an FMP endpoint with the user's key, classifying plan and key errors."""

    async def get(http: httpx2.AsyncClient) -> httpx2.Response:
        return await http.get(f"{FMP_BASE_URL}/{path}", params=params | {"apikey": api_key})

    if client is not None:
        response = await get(client)
    else:
        async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as own_client:
            response = await get(own_client)
    if response.status_code == 402:
        raise ProviderPlanError(
            f"Your Financial Modeling Prep plan does not cover this request ({path} for "
            f"{params.get('symbol', '')})."
        )
    if response.status_code in (401, 403):
        raise ProviderNotConfiguredError(
            "Financial Modeling Prep rejected your API key; update it with PUT "
            "/api/v1/me/credentials."
        )
    if response.status_code == 429:
        raise DataFetchError("Your Financial Modeling Prep plan's rate limit was reached.")
    response.raise_for_status()
    return response.json()


def _alpha_vantage_error(
    payload: Any, function: str, symbol: str, free: bool = False
) -> DataFetchError | None:
    """The error an Alpha Vantage answer reports, if any.

    Alpha Vantage answers every request with status 200 and reports problems in the body:
    ``Error Message`` for an unknown symbol, and ``Information`` or ``Note`` for a bad
    key, a rate limit or a function the plan does not cover. It answers an invalid key as
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
            "Alpha Vantage rejected your API key; update it with PUT /api/v1/me/credentials."
        )
    if "burst" in lowered or "per second" in lowered:
        return AlphaVantageBurstError(f"Alpha Vantage refused a burst of requests: {message}")
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
        DataFetchError: On rate limits, transport failures and unexpected answers.
    """
    symbol = params.get("symbol") or params.get("from_currency", "")
    cache_key = "alphavantage:" + ":".join(
        [function, *(f"{name}={value}" for name, value in sorted(params.items()))]
    )
    ttl = ALPHA_VANTAGE_TTL_SECONDS.get(function, HISTORY_TTL_SECONDS)
    digest = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    request = f"{function}:{params.get('outputsize', '')}"
    free = function in ALPHA_VANTAGE_FREE_FUNCTIONS
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
        query = {"function": function, **params, "apikey": api_key}
        for attempt in range(2):
            await _alpha_vantage_slot()
            try:
                if client is not None:
                    response = await client.get(ALPHA_VANTAGE_URL, params=query)
                else:
                    async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as own_client:
                        response = await own_client.get(ALPHA_VANTAGE_URL, params=query)
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
        if error is None or isinstance(error, SymbolNotFoundError):
            text = json.dumps(payload)
            _remember_alpha_vantage(cache_key, text)
            await cache_set(cache_key, text, ttl)
    error = _alpha_vantage_error(payload, function, symbol, free)
    if error is not None:
        raise error
    result: dict[str, Any] = payload
    return result


def clear_alpha_vantage_memory() -> None:
    """Forget Alpha Vantage answers, plan refusals and request spacing kept in memory."""
    global _alpha_vantage_next_slot
    _alpha_vantage_memory.clear()
    _alpha_vantage_premium.clear()
    _alpha_vantage_next_slot = 0.0


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
    payload = await alpha_vantage_get("SYMBOL_SEARCH", {"keywords": symbol}, api_key, client)
    for match in payload.get("bestMatches") or []:
        if isinstance(match, dict) and str(match.get("1. symbol", "")).upper() == symbol:
            currency = match.get("8. currency")
            return str(match.get("2. name") or symbol), None if not currency else str(currency)
    raise SymbolNotFoundError(f"Alpha Vantage lists no {symbol!r}.")


async def _alpha_vantage_info(
    symbol: str, api_key: str, client: httpx2.AsyncClient | None
) -> TickerInfo:
    """Fetch ticker info from Alpha Vantage.

    With a paid plan, the company overview gives sector, industry and market cap. A free
    key gets the name and currency from the symbol search and the price from a quote, so
    valuation estimates the market cap from shares outstanding.
    """
    try:
        return await _alpha_vantage_overview(symbol, api_key, client)
    except ProviderPlanError:
        pass
    name, currency = await alpha_vantage_name(symbol, api_key, client)
    quote = await _alpha_vantage_quote(symbol, api_key, client)
    return TickerInfo(
        symbol=symbol, name=name, currency=currency, price=quote.price, source="alpha_vantage"
    )


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


async def _fill_info(info: TickerInfo, client: httpx2.AsyncClient | None) -> TickerInfo:
    """Fill the fields ``info``'s provider left empty from Alpha Vantage, if the user can."""
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
        Alpha Vantage when the user has a key for it, and named in ``filled_fields``.

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
            elif provider == "alpha_vantage":
                assert api_key is not None
                info = await _alpha_vantage_info(symbol, api_key, client)
            else:
                info = await asyncio.to_thread(_yfinance_info, symbol)
            await cache_set(key, info.model_dump_json(), INFO_TTL_SECONDS)
            return info

        return run

    info = await first_success(
        symbol, "ticker info", [(p, attempt(p, k)) for p, k in available_providers()]
    )
    return await _fill_info(info, client)


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
    """Combine bars into ``frequency`` bars, labelled by when each starts."""
    combined = frame.resample(frequency, label="left", closed="left").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    )
    return combined.dropna(subset=["Close"])


async def _fmp_history(
    symbol: str, period: str, interval: str, api_key: str, today: date | None = None
) -> pd.DataFrame:
    """Fetch OHLCV history from FMP: dividend-adjusted daily bars, or intraday charts.

    Bars longer than a day are built from daily bars. Intraday bars are split- but not
    dividend-adjusted, as FMP serves them.
    """
    zone = exchange_timezone(symbol)
    end = today or date.today()
    start = _window_start(period, end)
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
    symbol: str, period: str, interval: str, api_key: str, today: date | None = None
) -> pd.DataFrame:
    """Fetch OHLCV history from Alpha Vantage: adjusted daily bars, or intraday bars.

    Bars longer than a day are built from daily bars. Intraday bars cover about the last
    month, as Alpha Vantage serves them without a ``month`` parameter; they need a paid
    plan.

    Adjusted daily bars also need a paid plan. On a free key, the latest
    :data:`ALPHA_VANTAGE_FREE_DAILY_BARS` daily bars are served instead, unadjusted, and
    the frame's ``attrs`` carry a :data:`HISTORY_NOTICE` saying so; unless the yfinance
    fallback is on, which has the full adjusted history, so the plan error is passed on
    for it to answer.
    """
    end = today or date.today()
    start = pd.Timestamp(_window_start(period, end))
    limited = not get_settings().yfinance_fallback
    notice: str | None = None
    if interval in ALPHA_VANTAGE_INTRADAY:
        size, resample_to = ALPHA_VANTAGE_INTRADAY[interval]
        payload = await alpha_vantage_get(
            "TIME_SERIES_INTRADAY",
            {"symbol": symbol, "interval": size, "outputsize": "full"},
            api_key,
        )
        zone = str(payload.get("Meta Data", {}).get("6. Time Zone") or DEFAULT_TIMEZONE)
        frame = _alpha_vantage_frame(payload.get(f"Time Series ({size})") or {}, zone, False)
        frame = frame[frame.index >= start.tz_localize(zone)]
        return frame if resample_to is None or frame.empty else _resample(frame, resample_to)
    try:
        payload = await alpha_vantage_get(
            "TIME_SERIES_DAILY_ADJUSTED", {"symbol": symbol, "outputsize": "full"}, api_key
        )
        adjusted = True
    except ProviderPlanError:
        if not limited:
            raise
        payload = await alpha_vantage_get(
            "TIME_SERIES_DAILY", {"symbol": symbol, "outputsize": "compact"}, api_key
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
        ``Low``, ``Close`` and ``Volume`` columns, sorted ascending, de-duplicated, and
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
                if provider == "fmp":
                    assert api_key is not None
                    raw = await _fmp_history(symbol, period, interval, api_key)
                elif provider == "alpha_vantage":
                    assert api_key is not None
                    raw = await _alpha_vantage_history(symbol, period, interval, api_key)
                else:
                    raw = await asyncio.to_thread(_yfinance_history, symbol, period, interval)
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

    frame = await first_success(
        symbol, "price history", [(p, attempt(p, k)) for p, k in available_providers()]
    )
    if frame is None:
        # Providers return the same empty answer for an unknown symbol and for a real
        # one with no bars in the window, so ask for a snapshot to tell the two apart.
        await fetch_ticker_info(symbol)
        return pd.DataFrame(columns=OHLCV_COLUMNS, index=pd.DatetimeIndex([]), dtype=float)
    return frame


def _finite(value: object) -> float | None:
    """Return ``value`` as a float, mapping missing, NaN and infinite values to ``None``."""
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


async def _alpha_vantage_quote(
    symbol: str, api_key: str, client: httpx2.AsyncClient | None
) -> Quote:
    """Fetch the latest quote from Alpha Vantage's global quote."""
    payload = await alpha_vantage_get("GLOBAL_QUOTE", {"symbol": symbol}, api_key, client)
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
        ProviderPlanError: If the user's FMP plan does not cover ``symbol``.
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
                except ProviderPlanError:
                    # FMP answers quotes for unknown symbols with a plan error; its
                    # profile tells an unknown symbol apart from an excluded one.
                    await _fmp_info(symbol, api_key, client)
                    raise
            elif provider == "alpha_vantage":
                assert api_key is not None
                quote = await _alpha_vantage_quote(symbol, api_key, client)
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
        symbol, "a quote", [(p, attempt(p, k)) for p, k in available_providers()]
    )


def clear_quote_cache() -> None:
    """Forget cached quotes."""
    _quote_cache.clear()
