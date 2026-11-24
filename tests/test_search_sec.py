"""Tests for searching the SEC's lists of listed companies and of funds, which need no
API key."""

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import search as search_route
from app.data import fund_filings, sec_edgar
from app.data.fund_filings import search_fund_tickers
from app.data.sec_edgar import FilingFetchError, search_tickers
from app.main import app
from app.models.screener import SearchMatch

client = TestClient(app)


# In the SEC's order, by market value; Alphabet's other share classes come much later.
LISTED = [
    ("NVDA", "NVIDIA CORP", 1045810),
    ("GOOGL", "Alphabet Inc.", 1652044),
    ("GOOS", "Canada Goose Holdings Inc.", 1690511),
    ("GOOD", "GLADSTONE COMMERCIAL CORP", 1234006),
    ("GOOG", "Alphabet Inc.", 1652044),
]


@pytest.fixture
def sec_list(monkeypatch: pytest.MonkeyPatch) -> None:
    async def tickers(client: Any) -> tuple[dict[str, int], list[tuple[str, str]]]:
        return {t: cik for t, _, cik in LISTED}, [(t, name) for t, name, _ in LISTED]

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(sec_edgar, "_tickers", tickers)
    monkeypatch.setattr(sec_edgar, "edgar_client", Client)


@pytest.mark.usefixtures("sec_list")
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("GOO", ["GOOG", "GOOGL", "GOOD", "GOOS"]),
        ("goog", ["GOOG", "GOOGL"]),
        ("GOOGL", ["GOOGL"]),
        ("alphabet", ["GOOG", "GOOGL"]),
        ("nvidia", ["NVDA"]),
        ("zzzz", []),
    ],
)
def test_tickers_are_ranked_exact_then_prefix_then_name_by_company(
    query: str, expected: list[str]
) -> None:
    """An exact ticker comes first, then each company's share classes together, larger
    companies first and shorter tickers within one."""
    found = [ticker for ticker, _ in asyncio.run(search_tickers(query, 10))]

    if query == "GOO":
        # Alphabet ranks before the smaller companies, both its classes together.
        assert found[:2] == ["GOOG", "GOOGL"]
        assert set(found) == set(expected)
    else:
        assert found == expected


def test_the_sec_list_fills_search_when_nothing_else_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With an empty universe and no Finnhub key, typing the start of a ticker still
    suggests it."""

    async def sec(query: str, limit: int) -> list[tuple[str, str]]:
        return [("GOOG", "Alphabet Inc."), ("GOOGL", "Alphabet Inc.")]

    monkeypatch.setattr(search_route, "search_tickers", sec)

    body = client.get("/api/v1/search", params={"q": "GOO"}).json()

    assert [(m["symbol"], m["source"]) for m in body["items"]] == [
        ("GOOG", "sec"),
        ("GOOGL", "sec"),
    ]


def test_sec_matches_never_repeat_finnhub_or_universe_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def finnhub(query: str, limit: int) -> list[SearchMatch]:
        return [SearchMatch(symbol="GOOG", name="Alphabet Inc Class C", source="finnhub")]

    async def sec(query: str, limit: int) -> list[tuple[str, str]]:
        return [("GOOG", "Alphabet Inc."), ("GOOGL", "Alphabet Inc.")]

    monkeypatch.setattr(search_route, "_finnhub_matches", finnhub)
    monkeypatch.setattr(search_route, "search_tickers", sec)

    items = client.get("/api/v1/search", params={"q": "GOO"}).json()["items"]

    assert [(m["symbol"], m["source"]) for m in items] == [("GOOG", "finnhub"), ("GOOGL", "sec")]


def test_search_works_when_the_sec_cannot_be_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(query: str, limit: int) -> list[tuple[str, str]]:
        raise FilingFetchError("SEC EDGAR is down.")

    monkeypatch.setattr(search_route, "search_tickers", failing)

    response = client.get("/api/v1/search", params={"q": "GOO"})

    assert response.status_code == 200 and response.json()["items"] == []


FUND_TICKERS = {"VOO": "S000002839", "VOOG": "S000002840", "VTI": "S000002848", "VTIP": "S1"}


@pytest.fixture
def fund_list(monkeypatch: pytest.MonkeyPatch) -> None:
    async def tickers(client: Any) -> dict[str, str]:
        return FUND_TICKERS

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(fund_filings, "_fund_tickers", tickers)
    monkeypatch.setattr(fund_filings, "edgar_client", Client)


@pytest.mark.usefixtures("fund_list")
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("VOO", [("VOO", "Vanguard S&P 500 ETF"), ("VOOG", "Mutual fund or ETF")]),
        ("vti", [("VTI", "Vanguard Total Stock Market ETF"), ("VTIP", "Mutual fund or ETF")]),
        # A widely held fund is found by its name, even off the SEC's list.
        (
            "total stock",
            [
                ("VTI", "Vanguard Total Stock Market ETF"),
                ("VTSAX", "Vanguard Total Stock Market Index Fund Admiral Shares"),
            ],
        ),
    ],
)
def test_funds_are_found_by_ticker_and_by_a_known_name(
    query: str, expected: list[tuple[str, str]]
) -> None:
    assert asyncio.run(search_fund_tickers(query, 10)) == expected


def test_an_exact_fund_ticker_comes_first_however_many_companies_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Typing VTI finds the Vanguard fund before Virtuix (VTIX), a company."""

    async def companies(query: str, limit: int) -> list[tuple[str, str]]:
        return [("VTIX", "Virtuix Holdings Inc.")]

    async def funds(query: str, limit: int) -> list[tuple[str, str]]:
        return [("VTI", "Vanguard Total Stock Market ETF"), ("VTIP", "Mutual fund or ETF")]

    monkeypatch.setattr(search_route, "search_tickers", companies)
    monkeypatch.setattr(search_route, "search_fund_tickers", funds)

    items = client.get("/api/v1/search", params={"q": "VTI"}).json()["items"]

    assert [m["symbol"] for m in items] == ["VTI", "VTIX", "VTIP"]


def test_an_exact_ticker_is_looked_up_even_when_the_universe_fills_the_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ten universe stocks start with VO, yet VO itself, a fund, is still found."""

    async def universe(session: Any, query: str, limit: int, details: bool) -> list[SearchMatch]:
        return [SearchMatch(symbol=f"VO{chr(65 + i)}", name="A company") for i in range(limit)]

    async def funds(query: str, limit: int) -> list[tuple[str, str]]:
        return [("VO", "Vanguard Mid-Cap ETF")]

    monkeypatch.setattr(search_route, "search_stocks", universe)
    monkeypatch.setattr(search_route, "search_fund_tickers", funds)

    items = client.get("/api/v1/search", params={"q": "VO"}).json()["items"]

    assert items[0]["symbol"] == "VO" and len(items) == 10
