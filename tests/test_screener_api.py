"""Tests for screening the stored universe through the API."""

import asyncio
from datetime import date
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import markets, research
from app.api.v1.endpoints import screener as screener_route
from app.data.fetcher import DataFetchError
from app.data.index_members import IndexMembers
from app.data.universe import Listing
from app.core.config import get_settings
from app.core.credentials import ProviderKeys, use_provider_keys
from app.db.screener import finish_run, replace_universe, start_run
from app.db.session import get_sessionmaker
from app.db.tables import ScreenerStockRecord
from app.main import app
from app.models.markets import EarningsEvent
from app.models.screener import ScreenedStock, ScreenField, ScreenMetrics

client = TestClient(app)


def _stock(symbol: str, rank: int, **metrics: Any) -> ScreenedStock:
    """A stock with neutral metrics, overriding any."""
    fields: dict[str, Any] = {
        "symbol": symbol,
        "name": f"{symbol} Inc.",
        "exchange": "NYSE",
        "cik": rank,
        "market_cap_source": "provider",
        "market_cap_rank": rank,
        "price": 50.0,
        "market_cap": 1e12 / rank,
        "avg_dollar_volume": 1e7,
    }
    return ScreenedStock(**(fields | metrics))


STOCKS = [
    _stock("BIG", 1, pe_ratio=30.0, momentum_12_1=0.2, beta_market=1.3, sector="Technology"),
    _stock("CHEAP", 2, pe_ratio=12.0, momentum_12_1=0.1, beta_market=0.8, sector="Finance"),
    _stock("LOSS", 3, pe_ratio=-8.0, momentum_12_1=-0.3, beta_market=1.1),
    _stock("THIN", 600, pe_ratio=15.0, momentum_12_1=0.05, beta_market=0.6, avg_dollar_volume=2e6),
]


def _store(stocks: list[ScreenedStock], error: str | None = None) -> None:
    """Store ``stocks`` as the universe and record a finished refresh."""

    async def run() -> None:
        async with get_sessionmaker()() as session:
            await replace_universe(session, stocks)
            run_id = await start_run(session)
            await finish_run(session, run_id, error=error, stocks=len(stocks))

    asyncio.run(run())


def _screen(**body: Any) -> dict[str, Any]:
    response = client.post("/api/v1/screener", json=body)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def _symbols(result: dict[str, Any]) -> list[str]:
    return [item["symbol"] for item in result["items"]]


def test_screen_fields_are_stored_columns() -> None:
    """Every field a screen can use is a metric and a column of the stored universe."""
    fields = set(get_args(ScreenField))
    assert fields == set(ScreenMetrics.model_fields)
    assert fields <= set(ScreenerStockRecord.__table__.columns.keys())


def test_unbuilt_universe_is_unavailable() -> None:
    """Before the first refresh the screener says how to build the universe."""
    response = client.post("/api/v1/screener", json={})

    assert response.status_code == 503
    assert "python -m app.jobs.screener" in response.json()["detail"]


def test_default_screen_lists_the_largest_first() -> None:
    _store(STOCKS)

    result = _screen()

    assert _symbols(result) == ["BIG", "CHEAP", "LOSS", "THIN"]
    assert [item["rank"] for item in result["items"]] == [1, 2, 3, 4]
    assert result["total"] == 4 and result["refreshed_at"] is not None


def test_filters_combine_and_drop_missing_values() -> None:
    """P/E < 20 and positive momentum keep only cheap winners."""
    _store(STOCKS)

    result = _screen(
        filters=[
            {"field": "pe_ratio", "max": 20},
            {"field": "momentum_12_1", "min": 0},
        ]
    )

    assert _symbols(result) == ["CHEAP", "THIN"]


@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        ({"max": 20}, ["CHEAP", "THIN"]),
        ({"min": -10, "max": 20}, ["CHEAP", "LOSS", "THIN"]),
        ({"min": -10, "max": 0}, ["LOSS"]),
    ],
)
def test_pe_filters_leave_out_losses_without_a_negative_min(
    bounds: dict[str, float], expected: list[str]
) -> None:
    """A P/E bound means a profitable company unless the minimum asks for losses."""
    _store(STOCKS)

    result = _screen(filters=[{"field": "pe_ratio"} | bounds])

    assert sorted(_symbols(result)) == expected


def test_factor_ranking_sorts_by_beta_with_paging() -> None:
    """Lowest market beta first, paged, with ranks continuing across pages."""
    _store(STOCKS)

    first = _screen(sort={"field": "beta_market", "descending": False}, limit=2)
    second = _screen(sort={"field": "beta_market", "descending": False}, limit=2, offset=2)

    assert _symbols(first) == ["THIN", "CHEAP"]
    assert _symbols(second) == ["LOSS", "BIG"]
    assert [item["rank"] for item in second["items"]] == [3, 4]
    assert first["total"] == 4


def test_stocks_without_the_sort_field_come_last() -> None:
    _store(STOCKS + [_stock("NEW", 4)])

    assert _symbols(_screen(sort={"field": "beta_market"})) == [
        "BIG",
        "LOSS",
        "CHEAP",
        "THIN",
        "NEW",
    ]


@pytest.mark.parametrize(
    ("descending", "expected"),
    [
        (False, ["CHEAP", "THIN", "BIG", "LOSS", "NEW"]),
        (True, ["BIG", "THIN", "CHEAP", "LOSS", "NEW"]),
    ],
)
def test_losses_sort_after_positive_pe_either_way(descending: bool, expected: list[str]) -> None:
    """A negative P/E is a loss, not the cheapest stock; one without a P/E comes last."""
    _store(STOCKS + [_stock("NEW", 4)])

    result = _screen(sort={"field": "pe_ratio", "descending": descending})

    assert _symbols(result) == expected


@pytest.mark.parametrize(
    ("universe", "symbols"),
    [
        ("large_cap", ["BIG", "CHEAP", "LOSS"]),
        ("broad_market", ["BIG", "CHEAP", "LOSS", "THIN"]),
        ("liquid", ["BIG", "CHEAP", "LOSS"]),
    ],
)
def test_universe_tiers(universe: str, symbols: list[str]) -> None:
    """Tiers cut the universe by market cap rank or dollar volume."""
    _store(STOCKS)

    assert _symbols(_screen(universe=universe)) == symbols


def test_a_nasdaq_screen_keeps_its_listings() -> None:
    _store(STOCKS[:2] + [_stock("NDQ", 5, exchange="Nasdaq")])

    assert _symbols(_screen(universe="nasdaq")) == ["NDQ"]


def test_an_sp500_screen_keeps_the_index_companies_share_classes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The index's companies come from its fund's filing; of a company with notes listed
    beside its shares, only the share classes the index holds, most traded first."""

    async def members(index: str, listings: list[Listing]) -> IndexMembers:
        assert index == "sp500"
        return IndexMembers(listings=[], classes={1: 1, 3: 2})

    monkeypatch.setattr(screener_route, "cached_index_members", members)
    _store(
        STOCKS
        + [
            _stock("LOSSB", 3, avg_dollar_volume=5e6),
            _stock("LOSSN", 3, avg_dollar_volume=1e4),  # Its notes, barely traded.
        ]
    )

    assert _symbols(_screen(universe="sp500")) == ["BIG", "LOSS", "LOSSB"]


def test_an_sp500_screen_without_the_secs_list_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def down(index: str, listings: list[Listing]) -> IndexMembers:
        raise DataFetchError("The SEC is down.")

    monkeypatch.setattr(screener_route, "cached_index_members", down)
    _store(STOCKS)

    response = client.post("/api/v1/screener", json={"universe": "sp500"})

    assert response.status_code == 503 and "Large cap" in response.json()["detail"]


def test_sector_filter() -> None:
    _store(STOCKS)

    assert _symbols(_screen(sectors=["Finance", "Energy"])) == ["CHEAP"]


@pytest.mark.parametrize(
    "body",
    [
        {"filters": [{"field": "pe_ratio"}]},
        {"filters": [{"field": "pe_ratio", "min": 5, "max": 1}]},
        {"filters": [{"field": "dividend_yield", "min": 0}]},
        {"sort": {"field": "name"}},
        {"universe": "global"},
        {"limit": 0},
    ],
)
def test_invalid_screens_are_rejected(body: dict[str, Any]) -> None:
    assert client.post("/api/v1/screener", json=body).status_code == 422


def test_status_reports_the_universe_and_failures() -> None:
    _store(STOCKS)
    _store(STOCKS, error="RuntimeError: SEC down")

    status = client.get("/api/v1/screener/status").json()

    assert status["stocks"] == 4
    assert status["refreshed_at"] is not None
    assert status["last_error"] == "RuntimeError: SEC down"


# A universe built from unlicensed data is shown only to the account allowed to see it.


def _store_run(stocks: list[ScreenedStock], licensed: bool) -> None:
    """Store ``stocks`` as the universe, from a refresh that was or was not licensed."""

    async def run() -> None:
        async with get_sessionmaker()() as session:
            await replace_universe(session, stocks)
            run_id = await start_run(session)
            await finish_run(session, run_id, stocks=len(stocks), licensed=licensed)

    asyncio.run(run())


def test_an_unlicensed_universe_is_hidden_from_other_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Other accounts get no screen, an empty status, and search without sizes or sectors,
    and nothing names where the data came from."""
    monkeypatch.setattr(get_settings(), "admin_email", "owner@example.com")
    _store_run(STOCKS, licensed=False)

    screened = client.post("/api/v1/screener", json={})
    status = client.get("/api/v1/screener/status").json()
    found = client.get("/api/v1/search", params={"q": "BIG"}).json()["items"]

    assert screened.status_code == 503
    assert screened.json()["detail"] == "The stock screener is not available yet."
    assert status == {**status, "stocks": 0, "sectors": [], "refreshed_at": None}
    assert found[0]["symbol"] == "BIG"
    assert found[0]["market_cap"] is None and found[0]["sector"] is None
    shown = (screened.text + str(status) + str(found)).lower()
    assert "yfinance" not in shown and "yahoo" not in shown and "nasdaq" not in shown


def test_the_admin_sees_an_unlicensed_universe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "admin_email", "tester@example.com")
    _store_run(STOCKS, licensed=False)

    assert _symbols(_screen())[:2] == ["BIG", "CHEAP"]
    assert client.get("/api/v1/search", params={"q": "BIG"}).json()["items"][0]["market_cap"]


def test_a_licensed_universe_is_shown_to_every_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "admin_email", "owner@example.com")
    _store_run(STOCKS, licensed=True)

    assert _symbols(_screen())[:2] == ["BIG", "CHEAP"]
    assert client.get("/api/v1/screener/status").json()["stocks"] == len(STOCKS)


def test_reports_and_the_calendar_leave_out_an_unlicensed_universe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside a request's handler too: peers for AI reports, sizes for the calendar."""
    _store_run(STOCKS, licensed=False)
    use_provider_keys(ProviderKeys(unlicensed=False))
    assert asyncio.run(research._peer_group("BIG")) is None
    event = EarningsEvent(symbol="BIG", date=date.today())
    assert asyncio.run(markets._major([event], min_market_cap=1.0)) == []

    use_provider_keys(ProviderKeys(unlicensed=True))
    assert asyncio.run(research._peer_group("BIG")) is not None
    assert [e.symbol for e in asyncio.run(markets._major([event], min_market_cap=1.0))] == ["BIG"]
    use_provider_keys(ProviderKeys())
