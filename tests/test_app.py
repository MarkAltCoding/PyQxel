"""Tests for application wiring: startup and shutdown, CORS, and the registered routes."""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app


def test_lifespan_starts_r_and_closes_the_ai_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """R starts before the first request and the Anthropic client closes at shutdown."""
    events: list[str] = []

    def fake_start_r() -> bool:
        events.append("start_r")
        return True

    async def fake_close() -> None:
        events.append("close")

    monkeypatch.setattr(main, "start_r", fake_start_r)
    monkeypatch.setattr(main, "close_anthropic_client", fake_close)

    with TestClient(app) as client:
        assert events == ["start_r"]
        assert client.get("/health").status_code == 200

    assert events == ["start_r", "close"]


def test_app_starts_without_r(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing R installation does not stop the app from serving."""
    monkeypatch.setattr(main, "start_r", lambda: False)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_cors_allows_configured_origins() -> None:
    """Browser clients on configured origins get CORS headers; others do not."""
    client = TestClient(app)

    allowed = client.get("/health", headers={"Origin": "http://localhost:3000"})
    blocked = client.get("/health", headers={"Origin": "https://evil.example"})

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-origin" not in blocked.headers


def test_every_client_route_is_registered() -> None:
    """The routes the macOS and iOS clients depend on appear in the OpenAPI schema."""
    paths = TestClient(app).get("/openapi.json").json()["paths"]

    expected = {
        "/health": "get",
        "/api/v1/stocks/{symbol}": "get",
        "/api/v1/stocks/{symbol}/history": "get",
        "/api/v1/stocks/{symbol}/volatility": "get",
        "/api/v1/stocks/{symbol}/analysis": "post",
        "/api/v1/stocks/{symbol}/backtest": "post",
    }
    for path, method in expected.items():
        assert method in paths.get(path, {}), f"{method.upper()} {path} is not registered"
