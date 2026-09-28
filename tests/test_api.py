"""Tests for the FastAPI application's core routes."""

from fastapi.testclient import TestClient

from app.main import __version__, app


def test_health_returns_ok() -> None:
    """The health check reports status, app name, and version."""
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "PyQxel", "version": __version__}
