"""Async market data fetchers.

yfinance is the primary source. Its API is synchronous, so calls are pushed onto a
worker thread with :func:`asyncio.to_thread` to keep the event loop free. When
yfinance fails and ``FINANCIAL_DATA_API_KEY`` is set, ticker info falls back to
Financial Modeling Prep over ``httpx``. Live quotes follow the same fallback and are
cached for a few seconds, so connections polling the same symbol share one request.
Ticker info and price history are cached in Redis when ``REDIS_URL`` is set, so
workers and repeated requests share a provider call.

Providers answer an unknown symbol with an empty result rather than an error, which
is reported as :class:`SymbolNotFoundError`. Transport and rate-limit failures raise
inside the provider and are reported as the broader :class:`DataFetchError`.
"""

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd
import yfinance as yf

from app.core.cache import cache_get, cache_set
from app.core.config import get_settings
from app.models.quote import Quote
from app.models.stock import TickerInfo

logger = logging.getLogger(__name__)

FMP_PROFILE_URL: str = "https://financialmodelingprep.com/stable/profile"
FMP_QUOTE_URL: str = "https://financialmodelingprep.com/stable/quote"
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

_quote_cache: dict[str, tuple[float, Quote]] = {}


class DataFetchError(RuntimeError):
    """Raised when market data cannot be retrieved from any provider."""


class SymbolNotFoundError(DataFetchError):
    """Raised when a provider answers successfully but has no data for the symbol."""


def _normalize_symbol(symbol: str) -> str:
    """Return ``symbol`` stripped and upper-cased, rejecting empty input."""
    cleaned = symbol.strip().upper()
    if not cleaned:
        raise ValueError("Ticker symbol must not be empty.")
    return cleaned


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


async def _fmp_info(symbol: str, api_key: str, client: httpx.AsyncClient) -> TickerInfo:
    """Fetch ticker info from the Financial Modeling Prep company profile endpoint."""
    response = await client.get(FMP_PROFILE_URL, params={"symbol": symbol, "apikey": api_key})
    response.raise_for_status()
    payload: Any = response.json()
    if not isinstance(payload, list) or not payload:
        raise SymbolNotFoundError(f"FMP has no profile for {symbol!r}.")
    profile: dict[str, Any] = payload[0]
    return TickerInfo(
        symbol=symbol,
        name=profile.get("companyName"),
        currency=profile.get("currency"),
        exchange=profile.get("exchange") or profile.get("exchangeShortName"),
        sector=profile.get("sector"),
        industry=profile.get("industry"),
        market_cap=profile.get("marketCap") or profile.get("mktCap"),
        price=profile.get("price"),
        source="fmp",
    )


async def fetch_ticker_info(symbol: str, client: httpx.AsyncClient | None = None) -> TickerInfo:
    """Fetch a descriptive and pricing snapshot for ``symbol``.

    Args:
        symbol: Ticker symbol, e.g. ``"AAPL"``. Case and surrounding whitespace are ignored.
        client: Optional shared HTTP client for the fallback provider. A short-lived
            client is created when omitted.

    Returns:
        The normalized ticker snapshot.

    Raises:
        ValueError: If ``symbol`` is empty.
        SymbolNotFoundError: If the providers that answered have no data for ``symbol``.
        DataFetchError: If every configured provider fails.
    """
    symbol = _normalize_symbol(symbol)
    key = f"info:{symbol}"
    cached = await cache_get(key)
    if cached is not None:
        try:
            return TickerInfo.model_validate_json(cached)
        except ValueError:
            logger.warning("Ignoring unreadable cached info for %s.", symbol)

    info = await _fetch_ticker_info_uncached(symbol, client)
    await cache_set(key, info.model_dump_json(), INFO_TTL_SECONDS)
    return info


async def _fetch_ticker_info_uncached(symbol: str, client: httpx.AsyncClient | None) -> TickerInfo:
    """Fetch ticker info from yfinance, falling back to FMP when a key is configured."""
    try:
        return await asyncio.to_thread(_yfinance_info, symbol)
    except Exception as exc:
        yf_error = exc
        logger.warning("yfinance info lookup failed for %s: %s", symbol, exc)

    api_key = get_settings().financial_data_api_key
    if api_key is None:
        if isinstance(yf_error, SymbolNotFoundError):
            raise SymbolNotFoundError(f"Unknown ticker symbol {symbol!r}.") from yf_error
        raise DataFetchError(f"Could not fetch info for {symbol!r}.") from yf_error

    try:
        if client is not None:
            return await _fmp_info(symbol, api_key.get_secret_value(), client)
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as own_client:
            return await _fmp_info(symbol, api_key.get_secret_value(), own_client)
    except SymbolNotFoundError as exc:
        raise SymbolNotFoundError(f"Unknown ticker symbol {symbol!r}.") from exc
    except (httpx.HTTPError, DataFetchError, ValueError) as exc:
        raise DataFetchError(f"Could not fetch info for {symbol!r} from any provider.") from exc


def _yfinance_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Fetch OHLCV history from yfinance (blocking)."""
    return yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)


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
        period: yfinance lookback period (``"1mo"``, ``"1y"``, ``"max"``, ...).
        interval: yfinance bar size (``"1d"``, ``"1wk"``, ``"1h"``, ...).

    Returns:
        A date-indexed frame with ``Open``, ``High``, ``Low``, ``Close`` and ``Volume``
        columns, sorted ascending, de-duplicated, and without rows lacking a close. The
        frame is empty when ``symbol`` exists but has no bars in the requested window.

    Raises:
        ValueError: If ``symbol`` is empty.
        SymbolNotFoundError: If no bars are returned and ``symbol`` is unknown.
        DataFetchError: If the request fails, or the symbol's existence cannot be checked.
    """
    symbol = _normalize_symbol(symbol)
    key = f"history:{symbol}:{period}:{interval}"
    cached = await cache_get(key)
    if cached is not None:
        try:
            return _history_from_json(cached)
        except (ValueError, KeyError, TypeError):
            logger.warning("Ignoring unreadable cached history for %s.", symbol)

    try:
        raw = await asyncio.to_thread(_yfinance_history, symbol, period, interval)
    except Exception as exc:
        raise DataFetchError(f"Could not fetch history for {symbol!r}.") from exc

    if raw.empty:
        # yfinance returns the same empty frame for an unknown symbol and for a real one
        # with no bars in the window, so ask for a quote to tell the two apart.
        await fetch_ticker_info(symbol)
        return pd.DataFrame(columns=OHLCV_COLUMNS, index=pd.DatetimeIndex([]), dtype=float)
    frame = _clean_history(raw)
    if not frame.empty:
        await cache_set(key, _history_to_json(frame), _history_ttl(interval))
    return frame


def _finite(value: object) -> float | None:
    """Return ``value`` as a float, mapping missing, NaN and infinite values to ``None``."""
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
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


async def _fmp_quote(symbol: str, api_key: str, client: httpx.AsyncClient) -> Quote:
    """Fetch the latest quote from the Financial Modeling Prep quote endpoint."""
    response = await client.get(FMP_QUOTE_URL, params={"symbol": symbol, "apikey": api_key})
    response.raise_for_status()
    payload: Any = response.json()
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


async def fetch_quote(symbol: str, client: httpx.AsyncClient | None = None) -> Quote:
    """Fetch the latest price of ``symbol`` and its change since the previous close.

    Quotes are cached for :data:`QUOTE_TTL_SECONDS`, so frequent pollers do not each
    hit the provider.

    Args:
        symbol: Ticker symbol, e.g. ``"AAPL"``. Case and surrounding whitespace are ignored.
        client: Optional shared HTTP client for the fallback provider. A short-lived
            client is created when omitted.

    Returns:
        The latest quote.

    Raises:
        ValueError: If ``symbol`` is empty.
        SymbolNotFoundError: If the providers that answered have no price for ``symbol``.
        DataFetchError: If every configured provider fails.
    """
    symbol = _normalize_symbol(symbol)
    cached = _quote_cache.get(symbol)
    if cached is not None and time.monotonic() - cached[0] < QUOTE_TTL_SECONDS:
        return cached[1]

    quote = await _fetch_quote_uncached(symbol, client)
    _quote_cache[symbol] = (time.monotonic(), quote)
    return quote


async def _fetch_quote_uncached(symbol: str, client: httpx.AsyncClient | None) -> Quote:
    """Fetch a quote, telling an unknown symbol apart from a provider failure.

    ``fast_info`` fails with the same internal errors for an unknown symbol as for an
    outage, and FMP's quote endpoint answers unknown symbols with a plan error, so when
    every provider fails the ticker snapshot is asked whether the symbol exists.
    """
    try:
        return await _quote_from_providers(symbol, client)
    except SymbolNotFoundError:
        raise
    except DataFetchError:
        await fetch_ticker_info(symbol, client)
        raise


async def _quote_from_providers(symbol: str, client: httpx.AsyncClient | None) -> Quote:
    """Fetch a quote from yfinance, falling back to FMP when a key is configured."""
    try:
        return await asyncio.to_thread(_yfinance_quote, symbol)
    except Exception as exc:
        yf_error = exc
        logger.warning("yfinance quote lookup failed for %s: %s", symbol, exc)

    api_key = get_settings().financial_data_api_key
    if api_key is None:
        if isinstance(yf_error, SymbolNotFoundError):
            raise SymbolNotFoundError(f"Unknown ticker symbol {symbol!r}.") from yf_error
        raise DataFetchError(f"Could not fetch a quote for {symbol!r}.") from yf_error

    try:
        if client is not None:
            return await _fmp_quote(symbol, api_key.get_secret_value(), client)
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as own_client:
            return await _fmp_quote(symbol, api_key.get_secret_value(), own_client)
    except SymbolNotFoundError as exc:
        raise SymbolNotFoundError(f"Unknown ticker symbol {symbol!r}.") from exc
    except (httpx.HTTPError, DataFetchError, ValueError) as exc:
        raise DataFetchError(f"Could not fetch a quote for {symbol!r} from any provider.") from exc
