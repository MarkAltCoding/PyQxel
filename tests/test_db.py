"""Tests for the results database: its migrations, and storing backtest results.

Each test runs against the empty in-memory SQLite database set up in ``conftest``,
which ``init_db`` has migrated to the latest revision.
"""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session
from app.db.backtests import delete_backtest, get_backtest, list_backtests, save_backtest
from app.db.session import (
    MIGRATIONS_DIR,
    close_database,
    configure_database,
    get_engine,
    get_sessionmaker,
    init_db,
)
from app.db.tables import Base
from app.models.backtest import (
    BacktestResponse,
    BuyAndHold,
    EquityPoint,
    PerformanceMetrics,
    SmaCrossover,
)

pytestmark = pytest.mark.asyncio

START = datetime(2025, 1, 2, 5, tzinfo=timezone.utc)


def _metrics(total_return: float, sharpe: float | None = 1.2) -> PerformanceMetrics:
    """Performance metrics with the given headline numbers."""
    return PerformanceMetrics(
        start=START,
        end=START + timedelta(days=2),
        observations=2,
        total_return=total_return,
        annualized_volatility=0.2,
        sharpe_ratio=sharpe,
        max_drawdown=-0.1,
    )


def _result(
    symbol: str = "SPY",
    strategy: BuyAndHold | SmaCrossover | None = None,
    total_return: float = 0.05,
) -> BacktestResponse:
    """A small, unsaved backtest response."""
    return BacktestResponse(
        symbol=symbol,
        period="1y",
        interval="1d",
        periods_per_year=252,
        strategy=strategy or SmaCrossover(fast=20, slow=50),
        cost_bps=5.0,
        risk_free_rate=0.0,
        metrics=_metrics(total_return),
        benchmark=_metrics(0.08, sharpe=None),
        trades=3,
        exposure=0.5,
        equity_curve=[
            EquityPoint(timestamp=START + timedelta(days=day), strategy=1 + day / 100, benchmark=1)
            for day in range(3)
        ],
        coverage="full",
    )


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A session on the test database."""
    async with get_sessionmaker()() as session:
        yield session


async def test_saved_backtest_reads_back_unchanged(session: AsyncSession) -> None:
    """A stored result is returned in full, with the ID and time it was saved under."""
    original = _result()

    saved = await save_backtest(session, original)
    loaded = await get_backtest(session, saved.id)  # type: ignore[arg-type]

    assert saved.id is not None
    assert saved.saved_at is not None and saved.saved_at.tzinfo is not None
    assert loaded == saved
    assert loaded.model_dump(exclude={"id", "saved_at"}) == original.model_dump(
        exclude={"id", "saved_at"}
    )


async def test_unknown_id_reads_as_none(session: AsyncSession) -> None:
    """Looking up an ID that was never stored returns ``None``."""
    assert await get_backtest(session, uuid4()) is None


async def test_list_is_newest_first_with_headline_metrics(session: AsyncSession) -> None:
    """Listings summarize each result without its curve, the latest first."""
    first = await save_backtest(session, _result(total_return=0.01))
    second = await save_backtest(session, _result(total_return=0.02))

    page = await list_backtests(session)

    assert page.total == 2
    assert [item.id for item in page.items] == [second.id, first.id]
    summary = page.items[0]
    assert summary.symbol == "SPY"
    assert summary.strategy == "sma_crossover"
    assert summary.total_return == 0.02
    assert summary.sharpe_ratio == 1.2
    assert summary.benchmark_total_return == 0.08
    assert summary.saved_at == second.saved_at


async def test_list_filters_and_pages(session: AsyncSession) -> None:
    """Symbol and strategy filters combine, and ``total`` counts every page."""
    for _ in range(3):
        await save_backtest(session, _result("SPY"))
    await save_backtest(session, _result("SPY", BuyAndHold()))
    await save_backtest(session, _result("QQQ"))

    spy = await list_backtests(session, symbol="spy", limit=2)
    crossover = await list_backtests(session, symbol="SPY", strategy="sma_crossover")
    last_page = await list_backtests(session, symbol="SPY", limit=2, offset=4)

    assert spy.total == 4 and len(spy.items) == 2
    assert all(item.symbol == "SPY" for item in spy.items)
    assert crossover.total == 3
    assert last_page.total == 4 and last_page.items == []


async def test_delete_removes_only_that_result(session: AsyncSession) -> None:
    """Deleting reports whether the ID existed and leaves other results alone."""
    kept = await save_backtest(session, _result())
    dropped = await save_backtest(session, _result())

    assert await delete_backtest(session, dropped.id) is True  # type: ignore[arg-type]
    assert await delete_backtest(session, dropped.id) is False  # type: ignore[arg-type]
    assert await get_backtest(session, dropped.id) is None  # type: ignore[arg-type]
    assert await get_backtest(session, kept.id) is not None  # type: ignore[arg-type]


async def test_file_database_persists_across_engines(tmp_path: Path) -> None:
    """A SQLite file, and its missing parent folder, outlive the engine that wrote them."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'nested' / 'results.db'}"
    configure_database(url)
    assert await init_db() is True
    async with get_sessionmaker()() as session:
        saved = await save_backtest(session, _result())
    await close_database()

    configure_database(url)
    async with get_sessionmaker()() as session:
        assert await get_backtest(session, saved.id) == saved  # type: ignore[arg-type]
    await close_database()


async def test_unreachable_database_does_not_fail_startup() -> None:
    """``init_db`` reports a database it cannot reach instead of raising."""
    configure_database("sqlite+aiosqlite:////nonexistent-root-dir/x/results.db")

    assert await init_db() is False


async def test_engine_is_created_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without explicit configuration, ``DATABASE_URL`` picks the database."""
    await close_database()
    monkeypatch.setattr(
        db_session, "get_settings", lambda: type("S", (), {"database_url": "sqlite+aiosqlite://"})
    )

    assert db_session.get_engine().url.render_as_string() == "sqlite+aiosqlite://"


def _alembic_config(connection: Connection) -> Config:
    """An Alembic config that migrates ``connection``, as ``init_db`` does."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["connection"] = connection
    return config


def _schema_drift(connection: Connection) -> list[object]:
    """Differences between the migrated schema and the ORM models."""
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    drift: list[object] = compare_metadata(context, Base.metadata)
    return drift


def _current_revision(connection: Connection) -> str | None:
    """The revision the database is at."""
    return MigrationContext.configure(connection).get_current_revision()


async def test_migrations_match_the_models() -> None:
    """The latest migration builds exactly the tables the models declare.

    Fails when a model changes without a migration; generate one with
    ``alembic revision --autogenerate -m "..."``.
    """
    async with get_engine().connect() as connection:
        assert await connection.run_sync(_schema_drift) == []


async def test_database_is_at_the_latest_revision() -> None:
    """``init_db`` records the head revision, and running it again changes nothing."""
    head = ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()

    assert await init_db() is True
    async with get_engine().connect() as connection:
        assert await connection.run_sync(_current_revision) == head


async def test_migrations_downgrade_and_upgrade_cleanly() -> None:
    """Every migration can be reverted to an empty database and reapplied."""
    async with get_engine().begin() as connection:
        await connection.run_sync(lambda sync: command.downgrade(_alembic_config(sync), "base"))
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    assert tables == ["alembic_version"]

    assert await init_db() is True
    async with get_engine().connect() as connection:
        assert await connection.run_sync(_schema_drift) == []
