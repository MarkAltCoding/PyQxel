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
    rates: dict[tuple[str, str], float] | None = None,
) -> None:
    """Fake the financials, ticker info and exchange rate fetches."""

    async def fake_financials(symbol: str) -> Financials:
        if isinstance(financials, Exception):
            raise financials
        return financials

    async def fake_info(symbol: str) -> TickerInfo:
        if isinstance(info, Exception):
            raise info
        return info

    async def fake_rate(base: str, quote: str = "USD") -> float:
        if (base, quote) not in (rates or {}):
            raise DataFetchError(f"No {base}/{quote} rate.")
        return (rates or {})[base, quote]

    monkeypatch.setattr(fundamentals_route, "fetch_financials_filled", fake_financials)
    monkeypatch.setattr(fundamentals_route, "fetch_ticker_info", fake_info)
    monkeypatch.setattr(fundamentals_route, "fetch_exchange_rate", fake_rate)


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


def test_statements_in_another_currency_are_converted(monkeypatch: pytest.MonkeyPatch) -> None:
    """IFRS statements in euros are converted to the dollar market cap at the latest rate."""
    financials = sample_financials(accounting_standard="IFRS", currency="EUR")
    _serve(monkeypatch, financials, rates={("EUR", "USD"): 2.0})

    body = client.get("/api/v1/stocks/AAPL/fundamentals").json()

    valuation = body["valuation"]
    assert body["financials"]["currency"] == "EUR"
    assert valuation["exchange_rate"] == 2.0
    # 1,000 / (100 EUR x 2); EV = 1,000 + 2 x (100 - 50) debt less cash.
    assert valuation["pe_ratio"] == 5.0
    assert valuation["enterprise_value"] == 1_100.0
    assert valuation["ev_to_ebitda"] == pytest.approx(1_100.0 / 300.0)
    assert valuation["net_margin"] == 0.25
    assert "latest rate of 2 USD per EUR" in valuation["notes"][0]


def test_without_an_exchange_rate_only_market_multiples_are_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Margins and returns need no rate; P/E and the other market multiples do."""
    financials = sample_financials(currency="TWD")
    _serve(monkeypatch, financials)

    valuation = client.get("/api/v1/stocks/AAPL/fundamentals").json()["valuation"]

    assert valuation["pe_ratio"] is None
    assert valuation["enterprise_value"] is None
    assert valuation["fcf_yield"] is None
    assert valuation["net_margin"] == 0.25
    assert valuation["return_on_equity"] == 0.4
    assert "no exchange rate to USD" in valuation["notes"][0]


def test_shares_do_not_estimate_a_foreign_issuers_market_cap() -> None:
    """A foreign issuer's ordinary shares are not its US depositary shares."""
    info = INFO.model_copy(update={"market_cap": None})

    fundamentals, _ = build_fundamentals(sample_financials(currency="EUR"), info, 1.1)

    assert fundamentals.valuation is None


def test_stale_and_lagging_figures_are_flagged() -> None:
    """Old statements, and items ending before the latest period, are called out."""
    financials = sample_financials(
        latest_period_end=date(2025, 1, 1),
        revenue=flow("Revenues", 400.0).model_copy(update={"ttm_end": date(2024, 10, 1)}),
    )

    _, notices = build_fundamentals(financials, INFO, today=date(2026, 1, 1))

    assert "may be out of date" in notices[0]
    assert notices[1] == "TTM revenue end before 2025-01-01, the latest period reported."


def test_annual_ifrs_statements_are_stale_only_after_a_missed_year() -> None:
    """A foreign issuer's 20-F is a year old before the next one is due."""
    financials = sample_financials(accounting_standard="IFRS", latest_period_end=date(2025, 1, 1))

    _, current = build_fundamentals(financials, INFO, today=date(2026, 1, 1))
    _, overdue = build_fundamentals(financials, INFO, today=date(2026, 6, 1))

    assert not any("out of date" in notice for notice in current)
    assert any("out of date" in notice for notice in overdue)
