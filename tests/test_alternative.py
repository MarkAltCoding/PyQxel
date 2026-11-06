"""Tests for a company's lobbying, government contracts, patents and SEC filings from
Finnhub, and for its as-reported financials standing in for EDGAR.

Finnhub is reached through a mock transport, so no test touches the network.
"""

from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.v1.endpoints import alternative as alternative_route
from app.core.credentials import ProviderKeys, use_provider_keys
from app.data import alpha_vantage, fetcher, finnhub
from app.data import alternative as alternative_data
from app.data.alternative import fetch_alternative, parse_submissions
from app.data.fetcher import DataFetchError, ProviderNotConfiguredError
from app.data.finnhub_financials import company_facts, fetch_finnhub_financials
from app.data.sec_edgar import EdgarNotConfiguredError
from app.main import app
from app.models.alternative import AlternativeRecords, GovernmentAward, LobbyingFiling, SecFiling
from app.models.fundamentals import Financials
from app.stats.alternative import alternative_context, summarize_alternative
from tests.financials import sample_financials

KEY = "fh-user-key"
TODAY = date(2026, 10, 25)

LOBBYING = {
    "data": [
        {"year": 2026, "period": "Q2", "income": None, "expenses": 2_000_000,
         "documentUrl": "https://lda.senate.gov/1"},
        {"year": 2026, "period": "Q1", "income": 40_000, "expenses": None},
        {"year": 2025, "period": "Q4", "income": None, "expenses": 1_500_000},
        {"period": "Q3"},
    ]
}  # fmt: skip
SPENDING = {
    "data": [
        {"recipientName": "LOCKHEED MARTIN CORP", "totalValue": 5e8, "actionDate": "2026-09-01",
         "awardingAgencyName": "Department of Defense", "awardDescription": "F-35 support"},
        {"recipientName": "SIKORSKY", "totalValue": 2e8, "actionDate": "2026-08-01",
         "awardingAgencyName": "Department of Defense"},
        {"recipientName": "ZETA", "totalValue": 1e7, "actionDate": "2026-07-01",
         "awardingAgencyName": "Department of Transportation"},
    ]
}  # fmt: skip
PATENTS = {
    "data": [
        {"description": "Storing encoded data", "filingDate": "2024-05-31 00:00:00",
         "publicationDate": "2026-09-19 00:00:00", "patentNumber": "US1", "filingStatus": "Patent",
         "patentType": "Utility"},
        {"description": "", "publicationDate": "2026-08-01 00:00:00"},
    ]
}  # fmt: skip
SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["4", "8-K", "4", "10-K"],
            "filingDate": ["2026-10-22", "2026-10-20", "2026-10-10", "2026-01-30"],
            "accessionNumber": ["0001-26-1", "0001-26-2", "0001-26-3", "0001-26-4"],
            "primaryDocument": ["form4.xml", "8k.htm", "form4.xml", "10k.htm"],
        }
    }
}
"""EDGAR's submissions of a company, cut to four filings, one older than 90 days."""


def _finnhub(
    overrides: dict[str, Callable[[httpx2.Request], httpx2.Response]] | None = None,
) -> httpx2.AsyncClient:
    routes: dict[str, Any] = {
        "stock/lobbying": LOBBYING,
        "stock/usa-spending": SPENDING,
        "stock/uspto-patent": PATENTS,
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path.removeprefix("/api/v1/")
        if overrides and path in overrides:
            return overrides[path](request)
        return httpx2.Response(200, json=routes[path])

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


@pytest.fixture(autouse=True)
def finnhub_user(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Act as a user with a Finnhub key, and nothing kept."""
    monkeypatch.setattr(fetcher, "yfinance_fallback_on", lambda: False)
    fetcher.clear_alpha_vantage_memory()
    finnhub.clear_signals_memory()
    use_provider_keys(ProviderKeys(finnhub=SecretStr(KEY)))

    async def edgar(symbol: str, today: date, client: object = None) -> list[SecFiling]:
        return parse_submissions(SUBMISSIONS, 936468, today - timedelta(days=90))

    monkeypatch.setattr(alternative_data, "fetch_sec_filings", edgar)
    yield
    use_provider_keys(ProviderKeys())


@pytest.mark.asyncio
async def test_every_part_is_read_and_summarized() -> None:
    """Lobbying is totaled by year, awards by agency, and filings counted by form."""
    records = await fetch_alternative("lmt", TODAY, _finnhub())
    data = summarize_alternative(records, datetime.now(timezone.utc))

    assert [(y.year, y.amount, y.filings) for y in data.lobbying_years] == [
        (2026, 2_040_000, 2),
        (2025, 1_500_000, 1),
    ]
    assert data.lobbying[0].in_house and not data.lobbying[1].in_house
    assert data.contracts_total == pytest.approx(7.1e8)
    assert [(a.agency, a.awards) for a in data.contract_agencies] == [
        ("Department of Defense", 2),
        ("Department of Transportation", 1),
    ]
    assert data.contracts[0].description == "F-35 support"
    assert data.patents_count == 1 and data.patents[0].number == "US1"
    assert data.filing_counts == {"4": 2, "8-K": 1}
    assert [f.form for f in data.filings] == ["4", "8-K", "4"]
    assert data.notice is None


@pytest.mark.asyncio
async def test_a_refused_part_is_left_out_with_a_notice() -> None:
    """Contracts the plan refuses leave no total, and the rest intact."""
    records = await fetch_alternative(
        "LMT", TODAY, _finnhub({"stock/usa-spending": lambda r: httpx2.Response(403)})
    )
    data = summarize_alternative(records, datetime.now(timezone.utc))

    assert data.contracts_total is None and data.contracts == []
    assert data.lobbying_years
    assert data.notice == "Left out: your Finnhub plan does not cover government contracts."


@pytest.mark.asyncio
async def test_without_a_key_the_sec_filings_still_come_from_edgar() -> None:
    """EDGAR needs no key, so its filings are shown, and the parts Finnhub supplies are
    noted as needing its key."""
    use_provider_keys(ProviderKeys())

    records = await fetch_alternative("LMT", TODAY, _finnhub())

    assert records.filings is not None and [f.form for f in records.filings] == ["4", "8-K", "4"]
    assert records.lobbying is None and len(records.missing) == 3
    assert "Finnhub key" in records.missing[0]


@pytest.mark.asyncio
async def test_without_a_key_or_edgar_alternative_data_needs_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(ProviderKeys())

    async def unreachable(symbol: str, today: date, client: object = None) -> None:
        raise DataFetchError("EDGAR is down.")

    monkeypatch.setattr(alternative_data, "fetch_sec_filings", unreachable)

    with pytest.raises(ProviderNotConfiguredError):
        await fetch_alternative("LMT", TODAY, _finnhub())


def test_edgar_submissions_give_the_recent_filings_with_links() -> None:
    filings = parse_submissions(SUBMISSIONS, 936468, date(2026, 7, 27))

    assert [(f.form, str(f.filed)) for f in filings] == [
        ("4", "2026-10-22"),
        ("8-K", "2026-10-20"),
        ("4", "2026-10-10"),
    ]
    assert filings[1].url == ("https://www.sec.gov/Archives/edgar/data/936468/0001262/8k.htm")


def test_context_gives_totals_and_8k_dates() -> None:
    """Claude gets the totals, largest awards and when 8-Ks were filed."""
    records = AlternativeRecords(
        symbol="LMT",
        lobbying=[LobbyingFiling(year=2026, in_house=True, amount=1e6)],
        contracts=[GovernmentAward(action_date=TODAY, recipient="LMT", value=1e8)],
        patents=[],
        filings=[],
    )
    data = summarize_alternative(records, datetime.now(timezone.utc))

    context = alternative_context(data, records)

    assert context.contracts_total == 1e8 and context.patents_count == 0
    assert context.recent_8k_dates == []


client = TestClient(app)


def test_route_without_key_serves_the_sec_filings(user: object) -> None:
    """Without a Finnhub key, the route serves EDGAR's filings and says what needs one."""
    response = client.get("/api/v1/stocks/LMT/alternative")

    assert response.status_code == 200
    body = response.json()
    assert body["filing_counts"] and "Finnhub key" in body["notice"]


def test_route_summarizes(user: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """The route serves the summary of what was fetched."""

    async def fake(symbol: str, today: date) -> AlternativeRecords:
        return AlternativeRecords(symbol=symbol, patents=[], contracts=[])

    monkeypatch.setattr(alternative_route, "fetch_alternative", fake)

    body = client.get("/api/v1/stocks/LMT/alternative").json()

    assert body["contracts_total"] == 0 and body["patents_count"] == 0


# As-reported financials.


def _report(
    form: str, start: str, end: str, filed: str, revenue: float, equity: float
) -> dict[str, Any]:
    return {
        "cik": "320193",
        "form": form,
        "startDate": f"{start} 00:00:00",
        "endDate": f"{end} 00:00:00",
        "filedDate": f"{filed} 00:00:00",
        "report": {
            "ic": [
                {"concept": "us-gaap_Revenues", "unit": "usd", "value": revenue},
                {"concept": "us-gaap_NetIncomeLoss", "unit": "usd", "value": revenue / 10},
                {"concept": "aapl_CompanyOwnThing", "unit": "usd", "value": 1.0},
            ],
            "bs": [{"concept": "us-gaap_StockholdersEquity", "unit": "usd", "value": equity}],
            "cf": [
                {"concept": "us-gaap_NetCashProvidedByUsedInOperatingActivities", "unit": "usd",
                 "value": revenue / 5},
            ],  # fmt: skip
        },
    }


REPORTS = [
    _report("10-K", "2024-10-01", "2025-09-30", "2025-11-01", 400.0, 60.0),
    _report("10-K", "2023-10-01", "2024-09-30", "2024-11-01", 380.0, 55.0),
    _report("10-Q", "2025-10-01", "2026-03-31", "2026-05-01", 210.0, 65.0),
    _report("10-Q", "2024-10-01", "2025-03-31", "2025-05-01", 200.0, 58.0),
]


def test_as_reported_filings_become_company_facts() -> None:
    """Period figures get their start, balance figures are at a date, own concepts drop."""
    facts = company_facts(REPORTS)["facts"]

    revenue = facts["us-gaap"]["Revenues"]["units"]["USD"]
    equity = facts["us-gaap"]["StockholdersEquity"]["units"]["USD"]
    assert revenue[0] == {
        "start": "2024-10-01",
        "end": "2025-09-30",
        "val": 400.0,
        "form": "10-K",
        "filed": "2025-11-01",
    }
    assert "start" not in equity[0]
    assert "aapl" not in facts


@pytest.mark.asyncio
async def test_as_reported_financials_give_ttm_figures() -> None:
    """The year to date and the last fiscal year give the TTM figure, as from EDGAR."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        annual = request.url.params["freq"] == "annual"
        return httpx2.Response(
            200, json={"data": [r for r in REPORTS if (r["form"] == "10-K") == annual]}
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(answer))

    financials = await fetch_finnhub_financials("AAPL", client)

    assert financials.source == "finnhub"
    assert financials.revenue is not None
    # 400 for the year to September 2025, plus 210 less 200 for the half-years since.
    assert financials.revenue.ttm == pytest.approx(410.0)
    assert financials.stockholders_equity is not None
    assert financials.stockholders_equity.value == 65.0


@pytest.mark.asyncio
async def test_finnhub_stands_in_for_edgar_before_alpha_vantage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When EDGAR cannot be read, the relayed filings are used, and Alpha Vantage is not asked."""
    use_provider_keys(ProviderKeys(finnhub=SecretStr(KEY), alpha_vantage=SecretStr("av")))

    async def no_edgar(symbol: str) -> Financials:
        raise EdgarNotConfiguredError("no user agent")

    async def relayed(symbol: str) -> Financials:
        return sample_financials().model_copy(update={"source": "finnhub"})

    async def alpha(symbol: str, api_key: str) -> Financials:
        raise AssertionError("Alpha Vantage was asked")

    monkeypatch.setattr(alpha_vantage, "fetch_financials", no_edgar)
    monkeypatch.setattr(alpha_vantage, "fetch_finnhub_financials", relayed)
    monkeypatch.setattr(alpha_vantage, "fetch_alpha_vantage_financials", alpha)

    financials = await alpha_vantage.fetch_financials_filled("AAPL")

    assert financials.source == "finnhub"
