"""Tests for searching the SEC's list of listed companies, which needs no API key."""

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import search as search_route
from app.data import sec_edgar
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
