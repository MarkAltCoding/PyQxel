"""Async SQLAlchemy engine and sessions for the results database.

``DATABASE_URL`` selects the backend: SQLite through ``aiosqlite`` by default, or
Postgres with a ``postgresql+asyncpg://`` URL. The schema is managed by the Alembic
migrations in ``app/db/migrations``, which :func:`init_db` applies at startup.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

logger = logging.getLogger(__name__)

MIGRATIONS_DIR: Path = Path(__file__).resolve().parent / "migrations"

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def create_engine_for(url: str) -> AsyncEngine:
    """Create an engine for ``url``, preparing SQLite's file or in-memory connection."""
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        if not get_settings().database_pgbouncer:
            return create_async_engine(url, pool_pre_ping=True)
        # PgBouncer in transaction mode hands each transaction any server connection, so
        # prepared statements must not be cached or reuse names across connections.
        return create_async_engine(
            parsed.update_query_dict({"prepared_statement_cache_size": "0"}),
            pool_pre_ping=True,
            connect_args={
                "statement_cache_size": 0,
                "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
            },
        )
    if parsed.database in (None, "", ":memory:"):
        # Each connection to an in-memory database gets its own empty database, so
        # every session must share one connection.
        return create_async_engine(
            url, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
    try:
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # Connecting will fail too, and is reported where connections are handled.
        logger.error("Cannot create the folder for %s: %s", parsed.database, exc)
    return create_async_engine(url)


def configure_database(url: str) -> AsyncEngine:
    """Point sessions at the database at ``url``, replacing any previous engine.

    The previous engine is not disposed; call :func:`close_database` first if needed.
    """
    global _engine, _sessionmaker
    _engine = create_engine_for(url)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine() -> AsyncEngine:
    """Return the engine, creating it from ``DATABASE_URL`` on first use."""
    if _engine is None:
        return configure_database(get_settings().database_url)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the session factory bound to :func:`get_engine`."""
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session for one request; a FastAPI dependency."""
    async with get_sessionmaker()() as session:
        yield session


def _alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


@lru_cache(maxsize=1)
def head_revision() -> str | None:
    """Return the newest migration's revision, which a current schema is at."""
    return ScriptDirectory.from_config(_alembic_config()).get_current_head()


def _upgrade(connection: Connection, revision: str) -> None:
    """Apply migrations up to ``revision`` on ``connection``."""
    config = _alembic_config()
    config.attributes["connection"] = connection
    command.upgrade(config, revision)


async def init_db(revision: str = "head") -> bool:
    """Migrate the database to ``revision``; return whether the database was reachable.

    An unreachable database is logged rather than raised, so the rest of the API
    still serves and backtests are returned without being stored.
    """
    try:
        async with get_engine().begin() as connection:
            await connection.run_sync(_upgrade, revision)
    except (SQLAlchemyError, OSError) as exc:
        logger.error("Backtest database unavailable; results will not be stored: %s", exc)
        return False
    return True


async def check_database(
    timeout_seconds: float = 3.0,
) -> Literal["ok", "unavailable", "outdated"]:
    """Check the database for a health check.

    Returns:
        ``"ok"`` when it answers and its schema is at the newest migration,
        ``"outdated"`` when it answers but migrations are pending, and
        ``"unavailable"`` when it does not answer within ``timeout_seconds``.
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            async with get_engine().connect() as connection:
                try:
                    version = await connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                except SQLAlchemyError:
                    # Connected, but no migration has ever run.
                    version = None
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        logger.warning("Database health check failed: %s", exc)
        return "unavailable"
    return "ok" if version == head_revision() else "outdated"


async def close_database() -> None:
    """Dispose of the engine's connection pool, if one was created."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
