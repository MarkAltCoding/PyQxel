"""Tests for the market data fetcher: whose key pays, which provider answers, and parsing.

FMP is reached through a mock transport and yfinance through fakes, so no test touches
the network.
"""

import asyncio
import math
from collections.abc import Callable, Iterator
from datetime import date
from types import SimpleNamespace
from typing import Any

import httpx2
import pandas as pd
import pytest
from fakeredis import FakeAsyncRedis
from pydantic import SecretStr

from app.core.cache import KEY_PREFIX, configure_cache
from app.core.config import Settings
from app.core.credentials import ProviderKeys, current_provider_keys, use_provider_keys
from app.data import fetcher
from app.data.fetcher import (
    DataFetchError,
    ProviderNotConfiguredError,
    ProviderPlanError,
    SymbolNotFoundError,
)
from app.models.stock import TickerInfo

pytestmark = pytest.mark.asyncio

PROFILE = [{"companyName": "Apple Inc.", "currency": "USD", "exchange": "NASDAQ",
            "sector": "Technology", "industry": "Consumer Electronics",
            "marketCap": 3.0e12, "price": 200.0}]  # fmt: skip


@pytest.fixture(autouse=True)
def no_provider_keys() -> Iterator[None]:
    """Start each test as a user without an FMP key."""
    use_provider_keys(ProviderKeys())
    yield
    use_provider_keys(ProviderKeys())


def _fallback(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    """Turn the yfinance development fallback on or off."""
    settings = Settings(yfinance_fallback=enabled)
    monkeypatch.setattr(fetcher, "get_settings", lambda: settings)


def _fmp_key(key: str = "fmp-user-key") -> None:
    """Act as a user whose FMP key is ``key``."""
    use_provider_keys(ProviderKeys(fmp=SecretStr(key)))


FmpHandler = Callable[[str, dict[str, str]], object]


def _serve_fmp(monkeypatch: pytest.MonkeyPatch, answer: FmpHandler) -> list[tuple[str, str]]:
    """Answer FMP requests with ``answer(path, params)``: a payload, or an ``httpx2.Response``.

    Returns the (path, api key) of each request.
    """
    requests: list[tuple[str, str]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path.removeprefix("/stable/")
        params = dict(request.url.params)
        requests.append((path, params.get("apikey", "")))
        result = answer(path, params)
        return result if isinstance(result, httpx2.Response) else httpx2.Response(200, json=result)

    transport = httpx2.MockTransport(handler)
    real_client = httpx2.AsyncClient

    def client(**kwargs: Any) -> httpx2.AsyncClient:
        return real_client(transport=transport)

    monkeypatch.setattr("app.data.fetcher.httpx2.AsyncClient", client)
    return requests


def _yfinance_info_returning(info: TickerInfo | Exception) -> Callable[[str], TickerInfo]:
    calls: list[str] = []

    def fake(symbol: str) -> TickerInfo:
        calls.append(symbol)
        if isinstance(info, Exception):
            raise info
        return info

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


YF_INFO = TickerInfo(symbol="AAPL", name="Apple Inc.", price=199.0, source="yfinance")


# Whose key pays, and which provider answers.


async def test_without_a_key_or_fallback_nothing_is_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user without an FMP key gets no data while the fallback is off."""
    _fallback(monkeypatch, False)
    monkeypatch.setattr(fetcher, "_yfinance_info", _yfinance_info_returning(YF_INFO))

    for call in (
        fetcher.fetch_ticker_info("AAPL"),
        fetcher.fetch_price_history("AAPL"),
        fetcher.fetch_quote("AAPL"),
    ):
        with pytest.raises(ProviderNotConfiguredError, match="PUT /api/v1/me/credentials"):
            await call


async def test_without_a_key_the_fallback_uses_yfinance(monkeypatch: pytest.MonkeyPatch) -> None:
    _fallback(monkeypatch, True)
    monkeypatch.setattr(fetcher, "_yfinance_info", _yfinance_info_returning(YF_INFO))

    info = await fetcher.fetch_ticker_info("aapl")

    assert info.source == "yfinance"


async def test_fmp_is_asked_first_with_the_users_own_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fallback(monkeypatch, True)
    _fmp_key("fmp-user-key")
    requests = _serve_fmp(monkeypatch, lambda path, params: PROFILE)
    yfinance = _yfinance_info_returning(YF_INFO)
    monkeypatch.setattr(fetcher, "_yfinance_info", yfinance)

    info = await fetcher.fetch_ticker_info("AAPL")

    assert (info.source, info.name, info.market_cap) == ("fmp", "Apple Inc.", 3.0e12)
    assert requests == [("profile", "fmp-user-key")]
    assert yfinance.calls == []  # type: ignore[attr-defined]


async def test_plan_errors_fall_back_only_when_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A request the user's plan excludes is a plan error, or yfinance's in development."""
    _fmp_key()
    _serve_fmp(monkeypatch, lambda path, params: httpx2.Response(402, text="Restricted Endpoint"))
    monkeypatch.setattr(fetcher, "_yfinance_info", _yfinance_info_returning(YF_INFO))

    _fallback(monkeypatch, False)
    with pytest.raises(ProviderPlanError, match="plan does not cover"):
        await fetcher.fetch_ticker_info("TLT")

    _fallback(monkeypatch, True)
    assert (await fetcher.fetch_ticker_info("TLT")).source == "yfinance"


@pytest.mark.parametrize(
    ("status", "error", "message"),
    [
        (401, ProviderNotConfiguredError, "rejected your API key"),
        (403, ProviderNotConfiguredError, "rejected your API key"),
        (429, DataFetchError, "rate limit"),
        (500, DataFetchError, "Could not fetch"),
    ],
)
async def test_fmp_failures_are_classified(
    monkeypatch: pytest.MonkeyPatch, status: int, error: type[Exception], message: str
) -> None:
    _fallback(monkeypatch, False)
    _fmp_key()
    _serve_fmp(monkeypatch, lambda path, params: httpx2.Response(status, json={}))

    with pytest.raises(error, match=message):
        await fetcher.fetch_ticker_info("AAPL")


async def test_unknown_to_every_provider_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _fallback(monkeypatch, True)
    _fmp_key()
    _serve_fmp(monkeypatch, lambda path, params: [])
    monkeypatch.setattr(
        fetcher, "_yfinance_info", _yfinance_info_returning(SymbolNotFoundError("none"))
    )

    with pytest.raises(SymbolNotFoundError, match="Unknown ticker symbol 'ZZZZ'"):
        await fetcher.fetch_ticker_info("ZZZZ")


async def test_concurrent_requests_keep_their_own_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two users' requests in flight together are each billed to their own key."""
    _fallback(monkeypatch, False)
    requests = _serve_fmp(monkeypatch, lambda path, params: PROFILE)

    async def as_user(key: str, symbol: str) -> str | None:
        _fmp_key(key)
        await asyncio.sleep(0)
        await fetcher.fetch_ticker_info(symbol)
        secret = current_provider_keys().fmp
        return None if secret is None else secret.get_secret_value()

    seen = list(await asyncio.gather(as_user("key-a", "AAA"), as_user("key-b", "BBB")))

    assert seen == ["key-a", "key-b"]
    assert sorted(requests) == [("profile", "key-a"), ("profile", "key-b")]
    assert current_provider_keys().fmp is None


async def test_fmp_data_is_not_cached_for_users_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user without an FMP key never reads what another user's plan paid for."""
    configure_cache(FakeAsyncRedis())
    _fallback(monkeypatch, True)
    _fmp_key()
    _serve_fmp(monkeypatch, lambda path, params: PROFILE)
    assert (await fetcher.fetch_ticker_info("AAPL")).source == "fmp"

    use_provider_keys(ProviderKeys())
    monkeypatch.setattr(fetcher, "_yfinance_info", _yfinance_info_returning(YF_INFO))

    assert (await fetcher.fetch_ticker_info("AAPL")).source == "yfinance"


# FMP history.


def _eod_rows() -> list[dict[str, Any]]:
    """Dividend-adjusted daily bars as FMP returns them: newest first."""
    days = pd.bdate_range("2026-09-21", "2026-10-02")
    return [
        {"symbol": "AAPL", "date": day.date().isoformat(), "adjOpen": 100.0 + i,
         "adjHigh": 101.0 + i, "adjLow": 99.0 + i, "adjClose": 100.5 + i, "volume": 1_000 + i}
        for i, day in reversed(list(enumerate(days)))
    ]  # fmt: skip


async def test_fmp_daily_history_is_adjusted_and_dated_in_new_york(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fmp_key()
    windows: list[dict[str, str]] = []

    def answer(path: str, params: dict[str, str]) -> object:
        windows.append(params)
        return _eod_rows()

    _serve_fmp(monkeypatch, answer)

    frame = await fetcher._fmp_history("AAPL", "1mo", "1d", "key", today=date(2026, 10, 2))

    assert list(frame.columns) == fetcher.OHLCV_COLUMNS
    index = pd.DatetimeIndex(frame.index)
    assert str(index.tz) == "America/New_York"
    assert index.is_monotonic_increasing
    assert frame["Close"].iloc[0] == 100.5 and len(frame) == 10
    assert (windows[0]["from"], windows[0]["to"]) == ("2026-09-02", "2026-10-02")


async def test_fmp_weekly_bars_are_built_from_daily_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_fmp(monkeypatch, lambda path, params: _eod_rows())

    weekly = await fetcher._fmp_history("AAPL", "1mo", "1wk", "key", today=date(2026, 10, 2))

    assert [day.date().isoformat() for day in pd.DatetimeIndex(weekly.index)] == [
        "2026-09-21",
        "2026-09-28",
    ]
    first_week = weekly.iloc[0]
    assert (first_week["Open"], first_week["Close"]) == (100.0, 104.5)
    assert (first_week["High"], first_week["Low"]) == (105.0, 99.0)
    assert first_week["Volume"] == sum(1_000 + i for i in range(5))


async def test_fmp_short_periods_keep_the_last_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_fmp(monkeypatch, lambda path, params: _eod_rows())

    frame = await fetcher._fmp_history("AAPL", "5d", "1d", "key", today=date(2026, 10, 2))

    assert len(frame) == 5 and frame["Close"].iloc[-1] == 109.5


async def test_fmp_intraday_bars_come_from_charts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 2-minute bar combines two 1-minute chart bars."""
    paths: list[str] = []
    rows = [
        {"date": f"2026-10-02 09:3{minute}:00", "open": 1.0 + minute, "high": 2.0 + minute,
         "low": 0.5 + minute, "close": 1.5 + minute, "volume": 10}
        for minute in range(4)
    ]  # fmt: skip

    def answer(path: str, params: dict[str, str]) -> object:
        paths.append(path)
        return rows

    _serve_fmp(monkeypatch, answer)

    frame = await fetcher._fmp_history("AAPL", "1d", "2m", "key", today=date(2026, 10, 2))

    assert paths == ["historical-chart/1min"]
    assert len(frame) == 2
    assert (frame["Open"].iloc[0], frame["Close"].iloc[0], frame["Volume"].iloc[0]) == (
        1.0,
        2.5,
        20,
    )


@pytest.mark.parametrize(
    ("symbol", "zone"),
    [("AAPL", "America/New_York"), ("BRK-B", "America/New_York"), ("7203.T", "Asia/Tokyo"),
     ("SHEL.L", "Europe/London"), ("SHOP.TO", "America/Toronto")],
)  # fmt: skip
async def test_exchange_time_zones(symbol: str, zone: str) -> None:
    assert fetcher.exchange_timezone(symbol) == zone


async def test_history_empty_for_unknown_symbol_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty answer is checked against the profile: unknown symbols are a 404."""
    _fallback(monkeypatch, False)
    _fmp_key()
    _serve_fmp(monkeypatch, lambda path, params: [])

    with pytest.raises(SymbolNotFoundError):
        await fetcher.fetch_price_history("ZZZZ")


async def test_history_empty_for_real_symbol_returns_empty_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fallback(monkeypatch, False)
    _fmp_key()
    _serve_fmp(monkeypatch, lambda path, params: PROFILE if path == "profile" else [])

    history = await fetcher.fetch_price_history("NEW", period="5d")

    assert history.empty and list(history.columns) == fetcher.OHLCV_COLUMNS


# FMP quotes.


async def test_fmp_quote_plan_error_for_unknown_symbol_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FMP answers quotes for unknown symbols with a plan error; the profile tells."""
    _fallback(monkeypatch, False)
    _fmp_key()
    _serve_fmp(
        monkeypatch,
        lambda path, params: [] if path == "profile" else httpx2.Response(402, text="Premium"),
    )

    with pytest.raises(SymbolNotFoundError):
        await fetcher.fetch_quote("ZZZZ")


async def test_fmp_quote_plan_error_for_real_symbol_is_a_plan_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fallback(monkeypatch, False)
    _fmp_key()
    _serve_fmp(
        monkeypatch,
        lambda path, params: PROFILE if path == "profile" else httpx2.Response(402, text="x"),
    )

    with pytest.raises(ProviderPlanError):
        await fetcher.fetch_quote("TLT")


async def test_fmp_quote_maps_fields_and_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _fallback(monkeypatch, False)
    _fmp_key()
    quote = [{"price": 202.0, "previousClose": 200.0, "dayHigh": 203.0, "dayLow": 199.0,
              "volume": 1_000}]  # fmt: skip
    requests = _serve_fmp(monkeypatch, lambda path, params: quote)

    first = await fetcher.fetch_quote("AAPL")
    second = await fetcher.fetch_quote("AAPL")

    assert (first.price, first.change, first.source) == (202.0, 2.0, "fmp")
    assert second == first
    assert len(requests) == 1


# yfinance parsing, used by the development fallback.


class _FakeTicker:
    """Stands in for ``yf.Ticker`` with a fixed ``info`` dict."""

    def __init__(self, info: dict[str, object]) -> None:
        self.info = info


async def test_yfinance_info_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    info = {
        "quoteType": "EQUITY",
        "longName": "Apple Inc.",
        "shortName": "Apple",
        "currency": "USD",
        "exchange": "NMS",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "marketCap": 3.0e12,
        "currentPrice": 200.0,
        "regularMarketPrice": 199.0,
    }
    monkeypatch.setattr("app.data.fetcher.yf.Ticker", lambda symbol: _FakeTicker(info))

    snapshot = fetcher._yfinance_info("AAPL")

    assert (snapshot.name, snapshot.price, snapshot.source) == ("Apple Inc.", 200.0, "yfinance")


@pytest.mark.parametrize("info", [{}, {"quoteType": "NONE"}])
async def test_yfinance_info_without_quote_is_not_found(
    monkeypatch: pytest.MonkeyPatch, info: dict[str, object]
) -> None:
    monkeypatch.setattr("app.data.fetcher.yf.Ticker", lambda symbol: _FakeTicker(info))

    with pytest.raises(SymbolNotFoundError):
        fetcher._yfinance_info("ZZZZ")


class _FakeFastTicker:
    """Stands in for ``yf.Ticker`` with only ``fast_info``."""

    def __init__(self, **fast_info: object) -> None:
        self.fast_info = SimpleNamespace(**fast_info)


async def test_yfinance_quote_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    ticker = _FakeFastTicker(
        last_price=202.0,
        previous_close=200.0,
        day_high=203.5,
        day_low=199.0,
        last_volume=51_000_000.0,
        currency="USD",
    )
    monkeypatch.setattr("app.data.fetcher.yf.Ticker", lambda symbol: ticker)

    quote = fetcher._yfinance_quote("AAPL")

    assert quote.change == pytest.approx(2.0) and quote.change_percent == pytest.approx(0.01)
    assert (quote.volume, quote.currency, quote.source) == (51_000_000, "USD", "yfinance")


async def test_yfinance_quote_without_price_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    ticker = _FakeFastTicker(
        last_price=math.nan,
        previous_close=None,
        day_high=None,
        day_low=None,
        last_volume=None,
        currency=None,
    )
    monkeypatch.setattr("app.data.fetcher.yf.Ticker", lambda symbol: ticker)

    with pytest.raises(SymbolNotFoundError):
        fetcher._yfinance_quote("ZZZZ")


# Cleaning and caching.


def _new_york_bars() -> pd.DataFrame:
    """Daily bars in New York time, unsorted and repeated, one missing its close."""
    index = pd.DatetimeIndex(
        ["2026-09-25", "2026-09-24", "2026-09-25", "2026-09-28"], tz="America/New_York"
    )
    return pd.DataFrame(
        {
            "Open": [100.0, 99.0, 100.5, 101.0],
            "High": [102.0, 100.0, 103.0, 102.0],
            "Low": [99.0, 98.0, 100.0, 100.0],
            "Close": [101.0, 99.5, 102.5, math.nan],
            "Volume": [1_000, 900, 1_200, 0],
            "Dividends": [0.0, 0.0, 0.0, 0.0],
        },
        index=index,
    )


def _yfinance_history_counting(frame: pd.DataFrame) -> tuple[Any, list[str]]:
    calls: list[str] = []

    def fake(symbol: str, period: str, interval: str) -> pd.DataFrame:
        calls.append(symbol)
        return frame

    return fake, calls


async def test_history_is_sorted_deduplicated_and_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bars come back sorted, one per timestamp, closes only; the cache keeps their zone."""
    redis = FakeAsyncRedis()
    configure_cache(redis)
    _fallback(monkeypatch, True)
    fake, calls = _yfinance_history_counting(_new_york_bars())
    monkeypatch.setattr(fetcher, "_yfinance_history", fake)

    first = await fetcher.fetch_price_history("spy")
    second = await fetcher.fetch_price_history("SPY")

    assert calls == ["SPY"]
    assert first["Close"].tolist() == [99.5, 102.5]
    assert list(first.columns) == fetcher.OHLCV_COLUMNS
    pd.testing.assert_frame_equal(second, first)
    assert str(pd.DatetimeIndex(second.index).tz) == "America/New_York"
    ttl = await redis.pttl(KEY_PREFIX + "history:yfinance:SPY:1y:1d")
    assert 0 < ttl <= fetcher.HISTORY_TTL_SECONDS * 1000


@pytest.mark.parametrize(
    ("interval", "ttl"),
    [
        ("1m", fetcher.INTRADAY_HISTORY_TTL_SECONDS),
        ("90m", fetcher.INTRADAY_HISTORY_TTL_SECONDS),
        ("1h", fetcher.INTRADAY_HISTORY_TTL_SECONDS),
        ("1d", fetcher.HISTORY_TTL_SECONDS),
        ("1mo", fetcher.HISTORY_TTL_SECONDS),
    ],
)
async def test_history_ttl_by_interval(interval: str, ttl: float) -> None:
    assert fetcher._history_ttl(interval) == ttl


async def test_failed_lookups_are_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeAsyncRedis()
    configure_cache(redis)
    _fallback(monkeypatch, True)
    monkeypatch.setattr(
        fetcher, "_yfinance_info", _yfinance_info_returning(SymbolNotFoundError("none"))
    )

    with pytest.raises(SymbolNotFoundError):
        await fetcher.fetch_ticker_info("ZZZZ")

    assert await redis.keys() == []


async def test_empty_symbol_is_rejected() -> None:
    with pytest.raises(ValueError):
        await fetcher.fetch_ticker_info("   ")
