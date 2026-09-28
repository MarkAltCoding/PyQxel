"""Async market data fetchers.

yfinance is the primary source. Its API is synchronous, so calls are pushed onto a
worker thread with :func:`asyncio.to_thread` to keep the event loop free. When
yfinance fails and ``FINANCIAL_DATA_API_KEY`` is set, ticker info falls back to
Financial Modeling Prep over ``httpx``.
"""

import asyncio
import logging
from typing import Any

import httpx
import pandas as pd
import yfinance as yf

from app.core.config import get_settings
from app.models.stock import TickerInfo

logger = logging.getLogger(__name__)

FMP_PROFILE_URL: str = "https://financialmodelingprep.com/stable/profile"
HTTP_TIMEOUT_SECONDS: float = 10.0
OHLCV_COLUMNS: list[str] = ["Open", "High", "Low", "Close", "Volume"]


class DataFetchError(RuntimeError):
    """Raised when market data cannot be retrieved from any provider."""


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
        raise DataFetchError(f"yfinance returned no data for {symbol!r}.")
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
        raise DataFetchError(f"FMP returned no profile for {symbol!r}.")
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
        DataFetchError: If every configured provider fails.
    """
    symbol = _normalize_symbol(symbol)
    try:
        return await asyncio.to_thread(_yfinance_info, symbol)
    except Exception as exc:
        yf_error = exc
        logger.warning("yfinance info lookup failed for %s: %s", symbol, exc)

    api_key = get_settings().financial_data_api_key
    if api_key is None:
        raise DataFetchError(f"Could not fetch info for {symbol!r}.") from yf_error

    try:
        if client is not None:
            return await _fmp_info(symbol, api_key.get_secret_value(), client)
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as own_client:
            return await _fmp_info(symbol, api_key.get_secret_value(), own_client)
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
        columns, sorted ascending, de-duplicated, and without rows lacking a close.

    Raises:
        ValueError: If ``symbol`` is empty.
        DataFetchError: If no usable history is returned.
    """
    symbol = _normalize_symbol(symbol)
    try:
        raw = await asyncio.to_thread(_yfinance_history, symbol, period, interval)
    except Exception as exc:
        raise DataFetchError(f"Could not fetch history for {symbol!r}.") from exc

    history = _clean_history(raw)
    if history.empty:
        raise DataFetchError(f"No price history returned for {symbol!r}.")
    return history
