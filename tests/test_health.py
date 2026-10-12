"""Tests for the health checks and the deployment settings they depend on."""

import asyncio
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.cache import configure_cache
from app.core.config import Settings, get_settings
from app.db.session import (
    check_database,
    close_database,
    configure_database,
    create_engine_for,
    head_revision,
    init_db,
)
from app.main import __version__, app
from app.stats import r_bridge

client = TestClient(app)


class _DownRedis(FakeAsyncRedis):
    """A Redis client whose server is unreachable."""

    async def ping(self, **kwargs: Any) -> Any:
        raise RedisConnectionError("Connection refused")


def _fresh_database() -> None:
    """Swap in an empty database; the test account's is already migrated."""
    asyncio.run(close_database())
    configure_database("sqlite+aiosqlite://")


@pytest.fixture
def migrated() -> None:
    """Migrate the test database, as the app does at startup."""
    assert asyncio.run(init_db())


def test_liveness_needs_nothing_but_the_process() -> None:
    """/health answers even when the database is unreachable."""
    asyncio.run(close_database())
    configure_database("sqlite+aiosqlite:////nonexistent-root-dir/x/results.db")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "PyQxel", "version": __version__}


def test_ready_when_the_database_is_migrated(migrated: None) -> None:
    """A migrated database without Redis is ready; Redis is optional."""
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["checks"]["database"] == "ok"
    assert response.json()["checks"]["redis"] == "not_configured"


def test_ready_needs_no_api_key(migrated: None, real_auth: None) -> None:
    """Load balancers and uptime monitors call the health checks without a key."""
    assert client.get("/health/ready").status_code == 200
    assert client.get("/health").status_code == 200


def test_unreachable_database_is_unavailable() -> None:
    """Traffic is turned away while the database cannot be reached."""
    asyncio.run(close_database())
    configure_database("sqlite+aiosqlite:////nonexistent-root-dir/x/results.db")

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["checks"]["database"] == "unavailable"


def test_unmigrated_database_is_outdated() -> None:
    """A reachable database with pending migrations cannot serve yet."""
    _fresh_database()

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "outdated"


def test_database_behind_on_migrations_is_outdated() -> None:
    """A database migrated to an older revision is reported as outdated."""
    _fresh_database()
    assert asyncio.run(init_db("0006"))

    assert asyncio.run(check_database()) == "outdated"
    assert head_revision() != "0006"


def test_redis_up_is_ready(migrated: None) -> None:
    """A reachable Redis is reported as ok."""
    configure_cache(FakeAsyncRedis())

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["redis"] == "ok"


def test_redis_down_is_degraded_but_serving(migrated: None) -> None:
    """Without Redis the API still serves, uncached, so it stays in rotation."""
    configure_cache(_DownRedis())

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["redis"] == "unavailable"


@pytest.mark.parametrize(
    ("started", "reported"),
    [(None, "not_started"), (True, "ok"), (False, "unavailable")],
)
def test_r_is_reported_without_affecting_readiness(
    migrated: None, monkeypatch: pytest.MonkeyPatch, started: bool | None, reported: str
) -> None:
    """R is optional: the image without it serves everything but GARCH."""
    monkeypatch.setattr(r_bridge, "_available", started)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["r"] == reported


def test_failed_errors_are_not_revealed() -> None:
    """The response names each dependency's state, never the error behind it."""
    asyncio.run(close_database())
    configure_database("sqlite+aiosqlite:////nonexistent-root-dir/x/results.db")

    body = client.get("/health/ready").text

    assert "nonexistent-root-dir" not in body


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgres://u:p%40ss@db:5432/app", "postgresql+asyncpg://u:p%40ss@db:5432/app"),
        ("postgresql://db/app?sslmode=require", "postgresql+asyncpg://db/app?ssl=require"),
        (
            "postgresql://db/app?sslmode=require&channel_binding=require",
            "postgresql+asyncpg://db/app?ssl=require",
        ),
        ("postgresql+asyncpg://db/app?ssl=require", "postgresql+asyncpg://db/app?ssl=require"),
        ("sqlite+aiosqlite:///data/x.db", "sqlite+aiosqlite:///data/x.db"),
    ],
)
def test_hosted_postgres_urls_use_asyncpg(given: str, expected: str) -> None:
    """URLs as hosting platforms hand them out work without editing."""
    assert Settings(database_url=given).database_url == expected


def test_secrets_pasted_with_a_newline_still_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """Values pasted into a secret manager often keep the Enter that ended them."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://db/app?sslmode=require\n")
    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", "key\n")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://db/app?ssl=require"
    assert settings.credentials_encryption_key is not None
    assert settings.credentials_encryption_key.get_secret_value() == "key"


def test_pgbouncer_mode_turns_off_prepared_statement_caching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behind PgBouncer, statements are neither cached nor named alike across connections."""
    monkeypatch.setattr(get_settings(), "database_pgbouncer", True)

    engine = create_engine_for("postgresql+asyncpg://db/app")

    assert engine.url.query["prepared_statement_cache_size"] == "0"


def test_direct_postgres_keeps_prepared_statement_caching() -> None:
    """Without PgBouncer the asyncpg defaults are kept."""
    engine = create_engine_for("postgresql+asyncpg://db/app")

    assert "prepared_statement_cache_size" not in engine.url.query
