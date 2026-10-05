"""Tests for screening the stored universe through the API."""

import asyncio
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient

from app.db.screener import finish_run, replace_universe, start_run
from app.db.session import get_sessionmaker
from app.db.tables import ScreenerStockRecord
from app.main import app
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
    _stock("LOSS", 3, pe_ratio=None, momentum_12_1=-0.3, beta_market=1.1),
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
    """P/E < 20 and positive momentum keep only cheap winners; a loss has no P/E."""
    _store(STOCKS)

    result = _screen(
        filters=[
            {"field": "pe_ratio", "max": 20},
            {"field": "momentum_12_1", "min": 0},
        ]
    )

    assert _symbols(result) == ["CHEAP", "THIN"]


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
    _store(STOCKS)

    assert _symbols(_screen(sort={"field": "pe_ratio", "descending": False})) == [
        "CHEAP",
        "THIN",
        "BIG",
        "LOSS",
    ]


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
