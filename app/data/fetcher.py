"""Async market data fetchers.

Financial Modeling Prep (FMP), a licensed provider, is the primary source, called with
the requesting user's own API key (see :mod:`app.core.credentials`), so each user's
data is billed to their own plan. yfinance, which scrapes Yahoo, is used only when
``YFINANCE_FALLBACK`` is on, for development: for users without an FMP key, and when
FMP fails or a user's plan does not cover a request. Without it, a user with no FMP key
gets :class:`ProviderNotConfiguredError`, and a request their plan excludes,
:class:`ProviderPlanError`.

Results are cached per provider, in Redis when ``REDIS_URL`` is set and for a few
seconds in memory for quotes. Data FMP returned is only served from the cache to users
who query FMP themselves, so no one reads licensed data on another user's plan.

Providers answer an unknown symbol with an empty result rather than an error, which is
reported as :class:`SymbolNotFoundError`. Transport and rate-limit failures are reported
as the broader :class:`DataFetchError`.
"""

import asyncio
import json
import logging
import math
import time
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

Provider = Literal["fmp", "yfinance"]

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

T = TypeVar("T")


class DataFetchError(RuntimeError):
    """Raised when market data cannot be retrieved from any provider."""


class SymbolNotFoundError(DataFetchError):
    """Raised when a provider answers successfully but has no data for the symbol."""


class ProviderNotConfiguredError(DataFetchError):
    """Raised when the user has no usable FMP key and the yfinance fallback is off."""


class ProviderPlanError(DataFetchError):
    """Raised when the user's FMP plan does not cover the request."""


def _normalize_symbol(symbol: str) -> str:
    """Return ``symbol`` stripped and upper-cased, rejecting empty input."""
    cleaned = symbol.strip().upper()
    if not cleaned:
        raise ValueError("Ticker symbol must not be empty.")
    return cleaned


def _providers() -> list[tuple[Provider, str | None]]:
    """The providers to try for the current user, in order, with the FMP key to use.

    Raises:
        ProviderNotConfiguredError: If the user has no FMP key and the fallback is off.
    """
    key = current_provider_keys().fmp
    providers: list[tuple[Provider, str | None]] = []
    if key is not None:
        providers.append(("fmp", key.get_secret_value()))
    if get_settings().yfinance_fallback:
        providers.append(("yfinance", None))
    if not providers:
        raise ProviderNotConfiguredError(
            "Market data is billed to your own Financial Modeling Prep plan: add your API "
            "key with PUT /api/v1/me/credentials."
        )
    return providers


async def _first_success(
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


async def _fmp_get(
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
    payload = await _fmp_get("profile", {"symbol": symbol}, api_key, client)
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
        The normalized ticker snapshot.

    Raises:
        ValueError: If ``symbol`` is empty.
        ProviderNotConfiguredError: If the user has no FMP key and the fallback is off.
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
            else:
                info = await asyncio.to_thread(_yfinance_info, symbol)
            await cache_set(key, info.model_dump_json(), INFO_TTL_SECONDS)
            return info

        return run

    return await _first_success(
        symbol, "ticker info", [(p, attempt(p, k)) for p, k in _providers()]
    )


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
        rows = await _fmp_get(f"historical-chart/{chart}", window, api_key, None)
        fields = {"Open": "open", "High": "high", "Low": "low", "Close": "close"}
        frame = _fmp_frame(
            rows if isinstance(rows, list) else [], fields | {"Volume": "volume"}, zone
        )
        return frame if resample_to is None or frame.empty else _resample(frame, resample_to)
    rows = await _fmp_get("historical-price-eod/dividend-adjusted", window, api_key, None)
    fields = {"Open": "adjOpen", "High": "adjHigh", "Low": "adjLow", "Close": "adjClose"}
    frame = _fmp_frame(rows if isinstance(rows, list) else [], fields | {"Volume": "volume"}, zone)
    if period in ("1d", "5d") and not frame.empty:
        frame = frame.tail(int(period[0]))
    if interval in LONG_INTERVALS and not frame.empty:
        frame = _resample(frame, LONG_INTERVALS[interval])
    return frame


def _clean_history(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep OHLCV columns, sort by date, and drop rows missing a close price."""
    missing = [column for column in OHLCV_COLUMNS if column not in frame.columns]
    if missing:
        raise DataFetchError(f"History is missing columns: {missing}.")
    cleaned = frame.loc[:, OHLCV_COLUMNS].sort_index()
    cleaned = cleaned[~cleaned.index.duplicated(keep="last")]
    return cleaned.dropna(subset=["Close"])


def _history_to_json(frame: pd.DataFrame) -> str:
    """Serialize a cleaned history frame, keeping its index's time zone."""
    index = pd.DatetimeIndex(frame.index)
    return json.dumps(
        {
            "tz": None if index.tz is None else str(index.tz),
            "name": index.name,
            "index": [timestamp.isoformat() for timestamp in index],
            "columns": {column: frame[column].tolist() for column in frame.columns},
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
    return pd.DataFrame(payload["columns"], index=index, columns=OHLCV_COLUMNS)


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
        in the window.

    Raises:
        ValueError: If ``symbol`` is empty.
        ProviderNotConfiguredError: If the user has no FMP key and the fallback is off.
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

    frame = await _first_success(
        symbol, "price history", [(p, attempt(p, k)) for p, k in _providers()]
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
    payload = await _fmp_get("quote", {"symbol": symbol}, api_key, client)
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
        ProviderNotConfiguredError: If the user has no FMP key and the fallback is off.
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

    return await _first_success(symbol, "a quote", [(p, attempt(p, k)) for p, k in _providers()])


def clear_quote_cache() -> None:
    """Forget cached quotes."""
    _quote_cache.clear()
