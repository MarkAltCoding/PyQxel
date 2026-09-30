"""Tests for how the market data fetcher classifies provider failures.

Provider calls are replaced with fakes so no test touches the network.
"""

import httpx
import pandas as pd
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.data import fetcher
from app.data.fetcher import DataFetchError, SymbolNotFoundError
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
