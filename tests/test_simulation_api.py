"""Tests for the portfolio simulation routes and their stored results.

Price downloads are replaced with fakes, so no test touches the network; results are
stored in the in-memory database set up in ``conftest``.
"""

import asyncio
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.v1.endpoints import portfolio
from app.data.fetcher import DataFetchError, SymbolNotFoundError
from app.data.panel import ClosePanel
from app.main import app

client = TestClient(app)

SIXTY_FORTY = [{"symbol": "SPY", "weight": 0.6}, {"symbol": "TLT", "weight": 0.4}]


def _closes(symbols: list[str], days: int = 1_310, seed: int = 0) -> pd.DataFrame:
    """Closes ending today with modestly correlated, fat-tailed returns."""
    rng = np.random.default_rng(seed)
    shocks = rng.standard_t(4, size=(days, len(symbols))) * 0.008 + 0.0003
    index = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=days)
    return pd.DataFrame(100 * np.cumprod(1 + shocks, axis=0), index=index, columns=symbols)


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    result: pd.DataFrame | Exception | None = None,
    timezones: dict[str, str | None] | None = None,
) -> list[list[str]]:
    """Fake the panel download, by default closes for whatever is asked; return the requests."""
    calls: list[list[str]] = []

    async def fake_panel(symbols: list[str], period: str, interval: str) -> ClosePanel:
        calls.append(symbols)
        if isinstance(result, Exception):
            raise result
        closes = _closes(symbols) if result is None else result
        zones = timezones or {symbol: "America/New_York" for symbol in symbols}
        return ClosePanel(closes=closes, timezones=zones)

    monkeypatch.setattr(portfolio, "fetch_close_panel", fake_panel)
    return calls


def _simulate(**options: object) -> dict[str, Any]:
    """Post a small 60/40 simulation with ``options`` and return the response body."""
    payload = {"holdings": SIXTY_FORTY, "paths": 500, "seed": 1, **options}
    response = client.post("/api/v1/portfolio/simulate", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_simulation_defaults_and_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """A portfolio alone gets a one-month, 10,000-path t copula simulation, stored."""
    calls = _serve(monkeypatch)

    response = client.post(
        "/api/v1/portfolio/simulate",
        json={"holdings": [{"symbol": "spy", "weight": 0.6}, {"symbol": "tlt", "weight": 0.4}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert calls == [["SPY", "TLT"]]
    assert body["id"] is not None and body["saved_at"] is not None
    assert body["holdings"] == SIXTY_FORTY
    assert body["observations"] == 1_309
    simulation = body["simulation"]
    assert (simulation["horizon"], simulation["paths"]) == (21, 10_000)
    assert (simulation["dependence"], simulation["marginals"]) == ("student_t", "empirical")
    assert simulation["initial_value"] == 10_000
    assert simulation["seed"] is not None
    assert len(simulation["fan_chart"]) == 22
    assert [measure["confidence"] for measure in simulation["risk"]] == [0.95, 0.99]
    assert [check["symbols"] for check in simulation["tail_checks"]] == [["SPY", "TLT"]]
    assert "Not investment advice" in body["disclaimer"]


def test_options_are_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Horizon, paths, models and starting value reach the simulation."""
    _serve(monkeypatch)

    simulation = _simulate(
        horizon=63,
        paths=300,
        dependence="empirical",
        marginals="student_t",
        initial_value=1_000_000,
    )["simulation"]

    assert (simulation["horizon"], simulation["paths"]) == (63, 300)
    assert (simulation["dependence"], simulation["marginals"]) == ("empirical", "student_t")
    assert simulation["fan_chart"][0]["p50"] == 1_000_000
    assert simulation["copula_degrees_of_freedom"] is None
    assert all(fit["degrees_of_freedom"] for fit in simulation["marginal_fits"])


def test_seed_reproduces_a_simulation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Posting a result's seed again gives the same simulation."""
    _serve(monkeypatch)

    first = _simulate(seed=None)["simulation"]
    again = _simulate(seed=first["seed"])["simulation"]

    assert again == first


def test_single_holding_is_simulated(monkeypatch: pytest.MonkeyPatch) -> None:
    """One asset at full weight needs no copula and has no pairs to check."""
    _serve(monkeypatch)

    simulation = _simulate(holdings=[{"symbol": "SPY", "weight": 1.0}])["simulation"]

    assert simulation["tail_checks"] == []
    assert simulation["copula_degrees_of_freedom"] is None


def test_markets_hours_apart_are_warned_about(monkeypatch: pytest.MonkeyPatch) -> None:
    """Holdings on exchanges hours apart carry the asynchronous trading warning."""
    _serve(monkeypatch, timezones={"SPY": "America/New_York", "7203.T": "Asia/Tokyo"})

    holdings = [{"symbol": "SPY", "weight": 0.5}, {"symbol": "7203.T", "weight": 0.5}]
    warnings = _simulate(holdings=holdings)["simulation"]["warnings"]

    assert any("time zones hours apart" in warning for warning in warnings)


def test_stored_simulation_is_read_back_listed_and_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored run reads back unchanged, lists with headline figures, and can be deleted."""
    _serve(monkeypatch)
    created = _simulate()

    stored = client.get(f"/api/v1/portfolio/simulations/{created['id']}")
    listing = client.get("/api/v1/portfolio/simulations").json()

    assert stored.status_code == 200 and stored.json() == created
    assert listing["total"] == 1
    item = listing["items"][0]
    simulation = created["simulation"]
    assert item == {
        "id": created["id"],
        "saved_at": created["saved_at"],
        "symbols": ["SPY", "TLT"],
        "horizon": 21,
        "paths": 500,
        "dependence": "student_t",
        "marginals": "empirical",
        "expected_return": simulation["expected_return"],
        "probability_of_loss": simulation["probability_of_loss"],
        "value_at_risk_95": simulation["risk"][0]["value_at_risk"],
        "conditional_value_at_risk_95": simulation["risk"][0]["conditional_value_at_risk"],
    }
    assert client.delete(f"/api/v1/portfolio/simulations/{created['id']}").status_code == 204
    assert client.get(f"/api/v1/portfolio/simulations/{created['id']}").status_code == 404
    assert client.delete(f"/api/v1/portfolio/simulations/{created['id']}").status_code == 404


def test_listing_filters_by_whole_symbol_and_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """A symbol filter matches holdings exactly, not as a prefix; pages count everything."""
    _serve(monkeypatch)
    spy = _simulate()
    spyg = _simulate(holdings=[{"symbol": "SPYG", "weight": 1.0}])
    both = _simulate(holdings=[{"symbol": "SPYG", "weight": 0.5}, {"symbol": "SPY", "weight": 0.5}])

    only_spy = client.get("/api/v1/portfolio/simulations", params={"symbol": "spy"}).json()
    page = client.get("/api/v1/portfolio/simulations", params={"limit": 1, "offset": 1}).json()

    assert [item["id"] for item in only_spy["items"]] == [both["id"], spy["id"]]
    assert page["total"] == 3 and [item["id"] for item in page["items"]] == [spyg["id"]]


def test_unknown_simulation_is_404() -> None:
    """IDs never stored are 404s, and IDs that are not UUIDs are 422s."""
    missing = client.get("/api/v1/portfolio/simulations/00000000-0000-4000-8000-000000000000")

    assert missing.status_code == 404
    assert client.get("/api/v1/portfolio/simulations/not-a-uuid").status_code == 422


def test_simulation_is_returned_unsaved_when_the_database_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database failure costs the caller only the ID, never the result."""
    _serve(monkeypatch)

    def down(*args: object) -> object:
        raise OperationalError("INSERT", {}, ConnectionError("database is down"))

    monkeypatch.setattr(portfolio, "save_simulation", down)

    body = _simulate()

    assert body["id"] is None and body["saved_at"] is None
    assert body["simulation"]["paths"] == 500


@pytest.mark.parametrize(
    ("path", "target"),
    [
        ("/api/v1/portfolio/simulations", "list_simulations"),
        ("/api/v1/portfolio/simulations/00000000-0000-4000-8000-000000000000", "get_simulation"),
    ],
)
def test_reading_while_the_database_is_down_is_503(
    monkeypatch: pytest.MonkeyPatch, path: str, target: str
) -> None:
    """Listing or reading stored simulations without a database is a 503."""

    def down(*args: object) -> object:
        raise OperationalError("SELECT", {}, ConnectionError("database is down"))

    monkeypatch.setattr(portfolio, target, down)

    response = client.get(path)

    assert response.status_code == 503
    assert response.json() == {"detail": "The simulation database is unavailable."}


def test_busy_server_turns_simulations_away(monkeypatch: pytest.MonkeyPatch) -> None:
    """With every slot taken past the wait, the request is a 503 with Retry-After."""
    _serve(monkeypatch)
    monkeypatch.setattr(portfolio, "_simulation_slots", asyncio.Semaphore(0))
    monkeypatch.setattr(portfolio, "SIMULATION_QUEUE_SECONDS", 0.01)

    response = client.post(
        "/api/v1/portfolio/simulate", json={"holdings": SIXTY_FORTY, "paths": 500}
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "10"


def test_slot_is_released_after_each_simulation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successive simulations each get a slot back, so a one-slot server keeps serving."""
    _serve(monkeypatch)
    monkeypatch.setattr(portfolio, "_simulation_slots", asyncio.Semaphore(1))
    monkeypatch.setattr(portfolio, "SIMULATION_QUEUE_SECONDS", 0.01)

    for _ in range(3):
        _simulate()


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (SymbolNotFoundError("Unknown ticker symbols: ZZZZ."), 404),
        (DataFetchError("Could not fetch price history for TLT."), 502),
    ],
)
def test_download_failures_map_to_statuses(
    monkeypatch: pytest.MonkeyPatch, error: Exception, status: int
) -> None:
    """Unknown symbols are 404s and provider failures 502s."""
    _serve(monkeypatch, error)

    response = client.post("/api/v1/portfolio/simulate", json={"holdings": SIXTY_FORTY})

    assert response.status_code == status
    assert response.json() == {"detail": str(error)}


def test_too_little_history_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """Holdings sharing too few days are rejected with the reason."""
    _serve(monkeypatch, _closes(["SPY", "TLT"], days=100))

    response = client.post("/api/v1/portfolio/simulate", json={"holdings": SIXTY_FORTY})

    assert response.status_code == 422
    assert "daily closes" in response.json()["detail"]


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"paths": 100_000, "horizon": 252}, "paths x horizon is 25,200,000"),
        (
            {
                "paths": 10_000,
                "horizon": 252,
                "holdings": [{"symbol": f"S{i}", "weight": 0.05} for i in range(20)],
            },
            "paths x horizon x assets",
        ),
        (
            {"holdings": [{"symbol": "SPY", "weight": 0.6}, {"symbol": "TLT", "weight": 0.3}]},
            "sum to 1",
        ),
        ({"paths": 50}, "greater than or equal to 100"),
        ({"horizon": 0}, "greater than or equal to 1"),
        ({"seed": -1}, "greater than or equal to 0"),
        ({"dependence": "clayton"}, "'gaussian', 'student_t' or 'empirical'"),
        ({"initial_value": 0}, "greater than 0"),
    ],
)
def test_invalid_or_oversized_requests_are_rejected_before_downloading(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, object], message: str
) -> None:
    """Bad settings and runs over the limits fail validation without fetching any prices."""
    calls = _serve(monkeypatch)

    response = client.post("/api/v1/portfolio/simulate", json={"holdings": SIXTY_FORTY, **options})

    assert response.status_code == 422
    assert message in str(response.json()["detail"])
    assert calls == []
