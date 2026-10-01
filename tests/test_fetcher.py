"""Tests for how the market data fetcher classifies provider failures.

Provider calls are replaced with fakes so no test touches the network.
"""

import math
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.data import fetcher
from app.data.fetcher import DataFetchError, SymbolNotFoundError
from app.models.quote import Quote
from app.models.stock import TickerInfo

pytestmark = pytest.mark.asyncio


def _use_fmp_key(monkeypatch: pytest.MonkeyPatch, key: str | None) -> None:
    """Configure the FMP fallback key seen by the fetcher."""
    settings = Settings(financial_data_api_key=SecretStr(key) if key else None)
    monkeypatch.setattr(fetcher, "get_settings", lambda: settings)


def _yf_info_raising(error: Exception) -> object:
    """Build a fake ``_yfinance_info`` that raises ``error``."""

    def fake(symbol: str) -> TickerInfo:
        raise error

    return fake


def _fmp_info_raising(error: Exception) -> object:
    """Build a fake ``_fmp_info`` that raises ``error``."""

    async def fake(symbol: str, api_key: str, client: httpx.AsyncClient) -> TickerInfo:
        raise error

    return fake


async def test_info_unknown_symbol_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """yfinance having no quote, with no fallback configured, means not found."""
    _use_fmp_key(monkeypatch, None)
    monkeypatch.setattr(fetcher, "_yfinance_info", _yf_info_raising(SymbolNotFoundError("none")))

    with pytest.raises(SymbolNotFoundError):
        await fetcher.fetch_ticker_info("ZZZZ")


async def test_info_transport_failure_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """A yfinance exception, with no fallback configured, is a general fetch error."""
    _use_fmp_key(monkeypatch, None)
    monkeypatch.setattr(fetcher, "_yfinance_info", _yf_info_raising(ConnectionError("down")))

    with pytest.raises(DataFetchError) as caught:
        await fetcher.fetch_ticker_info("AAPL")
    assert not isinstance(caught.value, SymbolNotFoundError)


async def test_info_unknown_to_both_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the fallback also has no profile, the symbol is not found."""
    _use_fmp_key(monkeypatch, "key")
    monkeypatch.setattr(fetcher, "_yfinance_info", _yf_info_raising(SymbolNotFoundError("none")))
    monkeypatch.setattr(fetcher, "_fmp_info", _fmp_info_raising(SymbolNotFoundError("none")))

    with pytest.raises(SymbolNotFoundError):
        await fetcher.fetch_ticker_info("ZZZZ")


async def test_info_fallback_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the fallback cannot answer, not-found cannot be confirmed and a fetch error is raised."""
    _use_fmp_key(monkeypatch, "key")
    monkeypatch.setattr(fetcher, "_yfinance_info", _yf_info_raising(SymbolNotFoundError("none")))
    request = httpx.Request("GET", fetcher.FMP_PROFILE_URL)
    transport_error = httpx.ConnectError("down", request=request)
    monkeypatch.setattr(fetcher, "_fmp_info", _fmp_info_raising(transport_error))

    with pytest.raises(DataFetchError) as caught:
        await fetcher.fetch_ticker_info("ZZZZ")
    assert not isinstance(caught.value, SymbolNotFoundError)


def _yf_history_returning(frame: pd.DataFrame) -> object:
    """Build a fake ``_yfinance_history`` that returns ``frame``."""
    return lambda symbol, period, interval: frame


def _info_check(monkeypatch: pytest.MonkeyPatch, error: Exception | None) -> list[str]:
    """Replace the existence check with a fake that raises ``error``; return its calls."""
    calls: list[str] = []

    async def fake(symbol: str) -> TickerInfo:
        calls.append(symbol)
        if error is not None:
            raise error
        return TickerInfo(symbol=symbol, source="test")

    monkeypatch.setattr(fetcher, "fetch_ticker_info", fake)
    return calls


EMPTY_YF_FRAME = pd.DataFrame(columns=[*fetcher.OHLCV_COLUMNS, "Adj Close"])


async def test_history_empty_for_unknown_symbol_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty frame for a symbol no provider recognizes means not found."""
    monkeypatch.setattr(fetcher, "_yfinance_history", _yf_history_returning(EMPTY_YF_FRAME))
    _info_check(monkeypatch, SymbolNotFoundError("none"))

    with pytest.raises(SymbolNotFoundError):
        await fetcher.fetch_price_history("ZZZZ")


async def test_history_empty_for_real_symbol_returns_empty_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty frame for a known symbol is returned as an empty OHLCV frame."""
    monkeypatch.setattr(fetcher, "_yfinance_history", _yf_history_returning(EMPTY_YF_FRAME))
    calls = _info_check(monkeypatch, None)

    history = await fetcher.fetch_price_history("aapl")

    assert calls == ["AAPL"]
    assert history.empty
    assert list(history.columns) == fetcher.OHLCV_COLUMNS


async def test_history_empty_when_existence_unknown_is_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the existence check itself fails, the result is a general fetch error."""
    monkeypatch.setattr(fetcher, "_yfinance_history", _yf_history_returning(EMPTY_YF_FRAME))
    _info_check(monkeypatch, DataFetchError("down"))

    with pytest.raises(DataFetchError) as caught:
        await fetcher.fetch_price_history("AAPL")
    assert not isinstance(caught.value, SymbolNotFoundError)


async def test_history_without_closes_returns_empty_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows that all lack a close leave no usable bars, without checking existence."""
    frame = pd.DataFrame(
        {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [float("nan")], "Volume": [0]},
        index=pd.DatetimeIndex(["2026-09-25"]),
    )
    monkeypatch.setattr(fetcher, "_yfinance_history", _yf_history_returning(frame))
    calls = _info_check(monkeypatch, None)

    history = await fetcher.fetch_price_history("AAPL")

    assert history.empty
    assert calls == []


async def test_empty_symbol_is_rejected() -> None:
    """Blank symbols fail before any provider is called."""
    with pytest.raises(ValueError, match="must not be empty"):
        await fetcher.fetch_ticker_info("   ")


class _FakeTicker:
    """Stands in for ``yf.Ticker`` with a fixed ``info`` dict."""

    def __init__(self, info: dict[str, object]) -> None:
        self.info = info


async def test_yfinance_info_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """yfinance fields map onto the snapshot, preferring long names and current prices."""
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
    monkeypatch.setattr(fetcher.yf, "Ticker", lambda symbol: _FakeTicker(info))

    snapshot = fetcher._yfinance_info("AAPL")

    assert snapshot == TickerInfo(
        symbol="AAPL",
        name="Apple Inc.",
        currency="USD",
        exchange="NMS",
        sector="Technology",
        industry="Consumer Electronics",
        market_cap=3.0e12,
        price=200.0,
        source="yfinance",
    )


@pytest.mark.parametrize("info", [{}, {"quoteType": "NONE"}])
async def test_yfinance_info_without_quote_is_not_found(
    monkeypatch: pytest.MonkeyPatch, info: dict[str, object]
) -> None:
    """An empty or ``NONE`` quote means yfinance does not know the symbol."""
    monkeypatch.setattr(fetcher.yf, "Ticker", lambda symbol: _FakeTicker(info))

    with pytest.raises(SymbolNotFoundError):
        fetcher._yfinance_info("ZZZZ")


def _fmp_client(
    payload: object, status: int = 200
) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    """Build a client whose FMP profile endpoint answers with ``payload``."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), requests


async def test_fmp_fallback_parses_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """When yfinance fails, the FMP profile is fetched with the key and mapped."""
    _use_fmp_key(monkeypatch, "secret")
    monkeypatch.setattr(fetcher, "_yfinance_info", _yf_info_raising(ConnectionError("down")))
    profile = {
        "companyName": "Apple Inc.",
        "currency": "USD",
        "exchangeShortName": "NASDAQ",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "mktCap": 3.0e12,
        "price": 200.0,
    }
    client, requests = _fmp_client([profile])

    snapshot = await fetcher.fetch_ticker_info("aapl", client=client)

    assert snapshot.source == "fmp"
    assert (snapshot.name, snapshot.exchange, snapshot.market_cap) == (
        "Apple Inc.",
        "NASDAQ",
        3.0e12,
    )
    assert requests[0].url.params["symbol"] == "AAPL"
    assert requests[0].url.params["apikey"] == "secret"


@pytest.mark.parametrize(("payload", "status"), [([], 200), ({"error": "x"}, 200)])
async def test_fmp_empty_profile_is_not_found(
    monkeypatch: pytest.MonkeyPatch, payload: object, status: int
) -> None:
    """An empty or non-list FMP answer means the symbol is unknown."""
    _use_fmp_key(monkeypatch, "secret")
    monkeypatch.setattr(fetcher, "_yfinance_info", _yf_info_raising(ConnectionError("down")))
    client, _ = _fmp_client(payload, status)

    with pytest.raises(SymbolNotFoundError):
        await fetcher.fetch_ticker_info("ZZZZ", client=client)


async def test_fmp_http_error_is_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An FMP error status, such as a bad key, is a general fetch error."""
    _use_fmp_key(monkeypatch, "bad")
    monkeypatch.setattr(fetcher, "_yfinance_info", _yf_info_raising(ConnectionError("down")))
    client, _ = _fmp_client({"Error Message": "Invalid API KEY."}, status=401)

    with pytest.raises(DataFetchError) as caught:
        await fetcher.fetch_ticker_info("AAPL", client=client)
    assert not isinstance(caught.value, SymbolNotFoundError)


async def test_history_provider_exception_is_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any yfinance failure while fetching history is a fetch error."""

    def failing(symbol: str, period: str, interval: str) -> pd.DataFrame:
        raise RuntimeError("rate limited")

    monkeypatch.setattr(fetcher, "_yfinance_history", failing)

    with pytest.raises(DataFetchError, match="history"):
        await fetcher.fetch_price_history("AAPL")


async def test_history_missing_columns_is_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A frame without the OHLCV columns cannot be used."""
    frame = pd.DataFrame({"Close": [1.0]}, index=pd.DatetimeIndex(["2026-09-25"]))
    monkeypatch.setattr(fetcher, "_yfinance_history", _yf_history_returning(frame))

    with pytest.raises(DataFetchError, match="missing columns"):
        await fetcher.fetch_price_history("AAPL")


async def test_history_is_sorted_and_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bars come back oldest first, one per timestamp, keeping the last duplicate."""
    frame = pd.DataFrame(
        {
            "Open": [3.0, 1.0, 2.0, 2.5],
            "High": [3.0, 1.0, 2.0, 2.5],
            "Low": [3.0, 1.0, 2.0, 2.5],
            "Close": [3.0, 1.0, 2.0, 2.5],
            "Volume": [30, 10, 20, 25],
            "Dividends": [0.0] * 4,
        },
        index=pd.DatetimeIndex(["2026-09-25", "2026-09-23", "2026-09-24", "2026-09-24"]),
    )
    monkeypatch.setattr(fetcher, "_yfinance_history", _yf_history_returning(frame))

    history = await fetcher.fetch_price_history("AAPL")

    assert list(history.columns) == fetcher.OHLCV_COLUMNS
    assert list(history["Close"]) == [1.0, 2.5, 3.0]


@pytest.fixture
def empty_quote_cache() -> None:
    """Start each quote test with no cached quotes."""
    fetcher._quote_cache.clear()


class _FakeFastTicker:
    """Stands in for ``yf.Ticker`` with only ``fast_info``."""

    def __init__(self, **fast_info: object) -> None:
        self.fast_info = SimpleNamespace(**fast_info)


async def test_yfinance_quote_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fast_info`` maps onto the quote, with the change derived from the previous close."""
    ticker = _FakeFastTicker(
        last_price=202.0,
        previous_close=200.0,
        day_high=203.5,
        day_low=199.0,
        last_volume=51_000_000.0,
        currency="USD",
    )
    monkeypatch.setattr(fetcher.yf, "Ticker", lambda symbol: ticker)

    quote = fetcher._yfinance_quote("AAPL")

    assert (quote.symbol, quote.price, quote.previous_close) == ("AAPL", 202.0, 200.0)
    assert quote.change == pytest.approx(2.0)
    assert quote.change_percent == pytest.approx(0.01)
    assert (quote.day_high, quote.day_low, quote.volume) == (203.5, 199.0, 51_000_000)
    assert (quote.currency, quote.source) == ("USD", "yfinance")


async def test_yfinance_quote_without_price_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """No last price means yfinance does not know the symbol; NaN fields become null."""
    ticker = _FakeFastTicker(
        last_price=math.nan,
        previous_close=None,
        day_high=None,
        day_low=None,
        last_volume=None,
        currency=None,
    )
    monkeypatch.setattr(fetcher.yf, "Ticker", lambda symbol: ticker)

    with pytest.raises(SymbolNotFoundError):
        fetcher._yfinance_quote("ZZZZ")


async def test_quote_without_previous_close_has_no_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing previous close leaves the change unknown rather than failing."""
    ticker = _FakeFastTicker(
        last_price=10.0,
        previous_close=math.nan,
        day_high=None,
        day_low=None,
        last_volume=None,
        currency="USD",
    )
    monkeypatch.setattr(fetcher.yf, "Ticker", lambda symbol: ticker)

    quote = fetcher._yfinance_quote("NEW")

    assert (quote.previous_close, quote.change, quote.change_percent) == (None, None, None)


@pytest.mark.usefixtures("empty_quote_cache")
async def test_quotes_are_cached_briefly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated lookups within the TTL reuse the quote; later ones fetch again."""
    calls: list[str] = []

    def fake_quote(symbol: str) -> Quote:
        calls.append(symbol)
        return fetcher._build_quote(symbol, price=10.0, previous_close=9.0, source="yfinance")

    clock = [1000.0]
    monkeypatch.setattr(fetcher, "_yfinance_quote", fake_quote)
    monkeypatch.setattr(fetcher.time, "monotonic", lambda: clock[0])

    first = await fetcher.fetch_quote("aapl")
    second = await fetcher.fetch_quote("AAPL ")
    clock[0] += fetcher.QUOTE_TTL_SECONDS + 1
    await fetcher.fetch_quote("AAPL")

    assert first is second
    assert calls == ["AAPL", "AAPL"]


@pytest.mark.usefixtures("empty_quote_cache")
async def test_quote_falls_back_to_fmp(monkeypatch: pytest.MonkeyPatch) -> None:
    """When yfinance fails, the FMP quote is fetched with the key and mapped."""
    _use_fmp_key(monkeypatch, "secret")

    def failing_quote(symbol: str) -> Quote:
        raise ConnectionError("down")

    monkeypatch.setattr(fetcher, "_yfinance_quote", failing_quote)
    client, requests = _fmp_client(
        [{"price": 99.0, "previousClose": 100.0, "dayHigh": 101.0, "dayLow": 98.5, "volume": 10}]
    )

    quote = await fetcher.fetch_quote("msft", client=client)

    assert quote.source == "fmp"
    assert (quote.price, quote.change_percent, quote.volume) == (99.0, pytest.approx(-0.01), 10)
    assert requests[0].url.params["symbol"] == "MSFT"
    assert str(requests[0].url).startswith(fetcher.FMP_QUOTE_URL)


@pytest.mark.usefixtures("empty_quote_cache")
async def test_quote_plan_error_for_unknown_symbol_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FMP's plan error for an unknown symbol is resolved by the snapshot's empty profile."""
    _use_fmp_key(monkeypatch, "secret")

    def failing_quote(symbol: str) -> Quote:
        raise KeyError("currentTradingPeriod")

    monkeypatch.setattr(fetcher, "_yfinance_quote", failing_quote)
    monkeypatch.setattr(fetcher, "_yfinance_info", _yf_info_raising(SymbolNotFoundError("none")))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/quote"):
            return httpx.Response(402, text="Premium Query Parameter")
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(SymbolNotFoundError):
        await fetcher.fetch_quote("ZZZZ", client=client)


@pytest.mark.usefixtures("empty_quote_cache")
@pytest.mark.parametrize(
    ("yf_error", "info", "expected"),
    [
        (SymbolNotFoundError("none"), None, SymbolNotFoundError),
        (KeyError("currentTradingPeriod"), SymbolNotFoundError("none"), SymbolNotFoundError),
        (KeyError("currentTradingPeriod"), ConnectionError("down"), DataFetchError),
        (ConnectionError("down"), TickerInfo(symbol="AAPL", source="yfinance"), DataFetchError),
    ],
)
async def test_quote_without_fallback_classifies_failures(
    monkeypatch: pytest.MonkeyPatch,
    yf_error: Exception,
    info: TickerInfo | Exception | None,
    expected: type[Exception],
) -> None:
    """Without a fallback, a failed quote is not found only when the symbol is unknown.

    Other quote failures are checked against the ticker snapshot, since ``fast_info``
    fails the same way for unknown symbols and outages.
    """
    _use_fmp_key(monkeypatch, None)

    def failing_quote(symbol: str) -> Quote:
        raise yf_error

    def fake_info(symbol: str) -> TickerInfo:
        assert info is not None, "the snapshot should not be consulted"
        if isinstance(info, Exception):
            raise info
        return info

    monkeypatch.setattr(fetcher, "_yfinance_quote", failing_quote)
    monkeypatch.setattr(fetcher, "_yfinance_info", fake_info)

    with pytest.raises(expected) as caught:
        await fetcher.fetch_quote("ZZZZ")
    assert isinstance(caught.value, SymbolNotFoundError) == (expected is SymbolNotFoundError)
