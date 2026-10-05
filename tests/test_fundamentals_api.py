"""Tests for the fundamentals route. Fakes stand in for EDGAR and the market data provider."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import fundamentals as fundamentals_route
from app.api.v1.endpoints.fundamentals import build_fundamentals
from app.data.fetcher import DataFetchError
from app.data.fundamentals import FinancialsNotFoundError
from app.data.sec_edgar import CompanyNotFoundError, EdgarNotConfiguredError, FilingFetchError
from app.main import app
from app.models.fundamentals import Financials
from app.models.stock import TickerInfo
from tests.financials import flow, sample_financials

client = TestClient(app)

INFO = TickerInfo(symbol="AAPL", currency="USD", price=100.0, market_cap=1_000.0, source="t")


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    financials: Financials | Exception,
    info: TickerInfo | Exception = INFO,
) -> None:
    """Fake the financials and ticker info fetches."""

    async def fake_financials(symbol: str) -> Financials:
        if isinstance(financials, Exception):
            raise financials
        return financials

    async def fake_info(symbol: str) -> TickerInfo:
        if isinstance(info, Exception):
            raise info
        return info

    monkeypatch.setattr(fundamentals_route, "fetch_financials", fake_financials)
    monkeypatch.setattr(fundamentals_route, "fetch_ticker_info", fake_info)


def test_fundamentals_and_valuation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The financials are returned with multiples from the provider's market cap."""
    _serve(monkeypatch, sample_financials())

    response = client.get("/api/v1/stocks/aapl/fundamentals")

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "AAPL"
    assert body["financials"]["revenue"]["ttm"] == 400.0
    assert body["valuation"]["pe_ratio"] == 10.0
    assert body["valuation"]["ev_to_ebitda"] == 7.0
    assert body["notice"] is None


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (EdgarNotConfiguredError("set SEC_USER_AGENT"), 503),
        (CompanyNotFoundError("no registrant"), 404),
        (FinancialsNotFoundError("no US GAAP data"), 404),
        (FilingFetchError("EDGAR is down"), 502),
    ],
)
def test_edgar_errors_map_to_statuses(
    monkeypatch: pytest.MonkeyPatch, error: Exception, status: int
) -> None:
    """Configuration, missing companies and EDGAR outages each get their own status."""
    _serve(monkeypatch, error)

    response = client.get("/api/v1/stocks/AAPL/fundamentals")

    assert response.status_code == status
    assert response.json()["detail"] == str(error)


def test_missing_market_data_leaves_only_the_valuation_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The financials are still returned when the price provider fails."""
    _serve(monkeypatch, sample_financials(), DataFetchError("provider down"))

    body = client.get("/api/v1/stocks/AAPL/fundamentals").json()

    assert body["financials"]["net_income"]["ttm"] == 100.0
    assert body["valuation"] is None
    assert "Market data for AAPL could not be fetched" in body["notice"]


def test_foreign_currency_listing_is_not_valued(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dollar financials are not divided into a price quoted in another currency."""
    info = INFO.model_copy(update={"symbol": "AAPL.MX", "currency": "MXN"})
    _serve(monkeypatch, sample_financials(), info)

    body = client.get("/api/v1/stocks/AAPL.MX/fundamentals").json()

    assert body["valuation"] is None
    assert "trades in MXN" in body["notice"]


def test_stale_and_lagging_figures_are_flagged() -> None:
    """Old statements, and items ending before the latest period, are called out."""
    financials = sample_financials(
        latest_period_end=date(2025, 1, 1),
        revenue=flow("Revenues", 400.0).model_copy(update={"ttm_end": date(2024, 10, 1)}),
    )

    _, notices = build_fundamentals(financials, INFO, today=date(2026, 1, 1))

    assert "may be out of date" in notices[0]
    assert notices[1] == "TTM revenue end before 2025-01-01, the latest period reported."
