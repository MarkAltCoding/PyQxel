"""Tests for the FastAPI application's routes.

Market data fetchers are replaced with fakes so no test touches the network.
"""

import math

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import stocks
from app.data.fetcher import DataFetchError, SymbolNotFoundError
from app.main import __version__, app
from app.models.stock import TickerInfo

client = TestClient(app)


def test_health_returns_ok() -> None:
    """The health check reports status, app name, and version."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "PyQxel", "version": __version__}


def test_ticker_info_returns_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ticker route returns the fetcher's snapshot unchanged."""
    snapshot = TickerInfo(symbol="AAPL", name="Apple Inc.", price=190.5, source="yfinance")

    async def fake_fetch(symbol: str) -> TickerInfo:
        assert symbol == "aapl"
        return snapshot

    monkeypatch.setattr(stocks, "fetch_ticker_info", fake_fetch)
    response = client.get("/api/v1/stocks/aapl")

    assert response.status_code == 200
    assert response.json() == snapshot.model_dump()


def test_ticker_info_maps_fetch_error_to_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider failures surface as 502 Bad Gateway with the error message."""

    async def failing_fetch(symbol: str) -> TickerInfo:
        raise DataFetchError("Could not fetch info for 'ZZZZ'.")

    monkeypatch.setattr(stocks, "fetch_ticker_info", failing_fetch)
    response = client.get("/api/v1/stocks/ZZZZ")

    assert response.status_code == 502
    assert response.json() == {"detail": "Could not fetch info for 'ZZZZ'."}


def test_ticker_info_maps_unknown_symbol_to_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """A symbol no provider recognizes surfaces as 404 Not Found."""

    async def missing_fetch(symbol: str) -> TickerInfo:
        raise SymbolNotFoundError("Unknown ticker symbol 'ZZZZ'.")

    monkeypatch.setattr(stocks, "fetch_ticker_info", missing_fetch)
    response = client.get("/api/v1/stocks/ZZZZ")

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown ticker symbol 'ZZZZ'."}


@pytest.mark.parametrize("symbol", ["BAD$SYM", "TOOLONGSYMBOL12345"])
def test_ticker_info_rejects_invalid_symbol(symbol: str) -> None:
    """Symbols with disallowed characters or excessive length are rejected before fetching."""
    response = client.get(f"/api/v1/stocks/{symbol}")

    assert response.status_code == 422


def test_price_history_returns_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    """History is serialized oldest first, with missing non-close values as null."""
    index = pd.DatetimeIndex(["2026-09-24", "2026-09-25"], tz="America/New_York")
    frame = pd.DataFrame(
        {
            "Open": [100.0, math.nan],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.5],
            "Close": [101.0, 102.5],
            "Volume": [1_000_000, 1_200_000],
        },
        index=index,
    )
    calls: list[tuple[str, str, str]] = []

    async def fake_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        calls.append((symbol, period, interval))
        return frame

    monkeypatch.setattr(stocks, "fetch_price_history", fake_history)
    response = client.get("/api/v1/stocks/msft/history", params={"period": "5d", "interval": "1d"})

    assert response.status_code == 200
    assert calls == [("msft", "5d", "1d")]
    body = response.json()
    assert body["symbol"] == "MSFT"
    assert body["period"] == "5d"
    assert body["interval"] == "1d"
    assert [bar["close"] for bar in body["bars"]] == [101.0, 102.5]
    assert body["bars"][1]["open"] is None
    assert body["bars"][0]["volume"] == 1_000_000
    assert body["bars"][0]["timestamp"].startswith("2026-09-24T00:00:00")
    assert body["coverage"] == "full"
    assert body["notice"] is None


def test_price_history_uses_default_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omitted query parameters default to one year of daily bars."""
    calls: list[tuple[str, str]] = []

    async def fake_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        calls.append((period, interval))
        return pd.DataFrame(
            {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [0]},
            index=pd.DatetimeIndex(["2026-09-25"]),
        )

    monkeypatch.setattr(stocks, "fetch_price_history", fake_history)
    response = client.get("/api/v1/stocks/SPY/history")

    assert response.status_code == 200
    assert calls == [("1y", "1d")]


def _daily_frame(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Build a business-day OHLCV frame from ``start`` to ``end`` with constant prices."""
    index = pd.bdate_range(start.normalize(), end.normalize())
    return pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 0}, index=index
    )


def _serve_history(monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame) -> None:
    """Make the history route return ``frame`` for any request."""

    async def fake_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        return frame

    monkeypatch.setattr(stocks, "fetch_price_history", fake_history)


def test_price_history_full_window_has_no_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bars starting at the window start report full coverage."""
    now = pd.Timestamp.now()
    _serve_history(monkeypatch, _daily_frame(now - pd.DateOffset(years=1), now))

    body = client.get("/api/v1/stocks/SPY/history", params={"period": "1y"}).json()

    assert body["coverage"] == "full"
    assert body["notice"] is None


def test_price_history_partial_window_shows_available_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recent listing returns every bar it has, with a notice about the missing span."""
    now = pd.Timestamp.now()
    listed = now - pd.DateOffset(months=2)
    frame = _daily_frame(listed, now)
    _serve_history(monkeypatch, frame)

    response = client.get("/api/v1/stocks/NEWCO/history", params={"period": "1y"})

    assert response.status_code == 200
    body = response.json()
    assert body["coverage"] == "partial"
    assert len(body["bars"]) == len(frame)
    assert f"before {frame.index[0].date()}" in body["notice"]


def test_price_history_empty_window_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real symbol with no bars in the window returns 200, no bars, and a notice."""
    empty = pd.DataFrame(
        columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex([])
    )
    _serve_history(monkeypatch, empty)

    response = client.get("/api/v1/stocks/AAPL/history", params={"period": "5d", "interval": "1m"})

    assert response.status_code == 200
    body = response.json()
    assert body["bars"] == []
    assert body["coverage"] == "none"
    assert body["notice"].startswith("No price data exists for AAPL")


@pytest.mark.parametrize("period", ["1d", "5d", "max"])
def test_price_history_open_ended_periods_report_full(
    monkeypatch: pytest.MonkeyPatch, period: str
) -> None:
    """Trading-day and open-ended periods are never flagged as partial."""
    now = pd.Timestamp.now()
    _serve_history(monkeypatch, _daily_frame(now - pd.DateOffset(months=2), now))

    body = client.get("/api/v1/stocks/NEWCO/history", params={"period": period}).json()

    assert body["coverage"] == "full"


def test_price_history_rejects_unknown_period() -> None:
    """Periods outside the yfinance vocabulary fail validation."""
    response = client.get("/api/v1/stocks/AAPL/history", params={"period": "7y"})

    assert response.status_code == 422


def test_price_history_maps_fetch_error_to_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """History provider failures surface as 502 Bad Gateway."""

    async def failing_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        raise DataFetchError("No price history returned for 'AAPL'.")

    monkeypatch.setattr(stocks, "fetch_price_history", failing_history)
    response = client.get("/api/v1/stocks/AAPL/history")

    assert response.status_code == 502


def test_price_history_maps_unknown_symbol_to_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty history for the requested window surfaces as 404 Not Found."""

    async def missing_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        raise SymbolNotFoundError("No price history found for 'ZZZZ' over '1y'.")

    monkeypatch.setattr(stocks, "fetch_price_history", missing_history)
    response = client.get("/api/v1/stocks/ZZZZ/history")

    assert response.status_code == 404
