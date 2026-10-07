"""Tests for storing AI analyses and reusing them instead of paying for new ones.

Each test runs against the empty in-memory database set up in ``conftest``, with
Redis off, so reuse is shown to need only the database. No test calls Claude.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.ai import cache as analysis_cache
from app.ai.cache import analysis_slot, cached_analysis, remember_analysis
from app.core.config import Settings
from app.db.analyses import get_analysis, list_analyses, save_analysis
from app.db.session import get_sessionmaker
from app.db.tables import AnalysisRecord
from app.models.research import (
    ANALYSIS_CONTEXT_VERSION,
    AnalysisContext,
    AnalysisRequest,
    AnalysisResponse,
    PriceSummary,
    RiskSummary,
)
from app.models.stock import TickerInfo

pytestmark = pytest.mark.asyncio

RISK = AnalysisRequest(kind="risk")


OWNER: str = "00000000-0000-0000-0000-000000000001"
"""The account the analyses belong to."""


def _use_settings(monkeypatch: pytest.MonkeyPatch, **values: object) -> Settings:
    """Make the analysis store see settings with ``values``."""
    settings = Settings(**values)  # type: ignore[arg-type]
    monkeypatch.setattr(analysis_cache, "get_settings", lambda: settings)
    return settings


def _response(
    symbol: str = "AAPL", age: timedelta = timedelta(0), headline: str = "Moderate risk."
) -> AnalysisResponse:
    """A finished risk summary, written ``age`` ago."""
    now = datetime.now(timezone.utc)
    return AnalysisResponse(
        symbol=symbol,
        kind="risk",
        report=RiskSummary(
            headline=headline,
            risk_level="moderate",
            summary="Volatility is near its average.",
            volatility_assessment="Average.",
            drawdown_assessment="Shallow.",
            key_risks=[],
            data_limitations=["Prices only."],
        ),
        context=AnalysisContext(
            ticker=TickerInfo(symbol=symbol, source="yfinance"),
            period="1y",
            prices=PriceSummary(
                start=now - timedelta(days=365),
                end=now,
                observations=250,
                first_close=100,
                last_close=110,
                high=120,
                low=90,
                period_return=0.1,
                realized_volatility=0.2,
                max_drawdown=-0.15,
                current_drawdown=-0.05,
                best_return=0.04,
                worst_return=-0.05,
            ),
            coverage="full",
        ),
        model="claude-opus-5-5",
        generated_at=now - age,
    )


def _database_down(*args: object, **kwargs: object) -> object:
    """Stand in for a storage call while the database is unreachable."""
    raise OperationalError("SELECT 1", {}, ConnectionError("database is down"))


async def test_stored_analysis_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored report comes back for the same request with its ID, marked as cached."""
    _use_settings(monkeypatch)

    saved = await remember_analysis(OWNER, RISK, _response())
    reused = await cached_analysis(OWNER, "aapl", RISK)

    assert saved.id is not None and saved.cached is False
    assert reused is not None
    assert reused.cached is True
    assert reused.model_copy(update={"cached": False}) == saved


async def test_reports_older_than_the_ttl_are_not_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only reports written within ``ANALYSIS_CACHE_TTL_SECONDS`` are reused."""
    await remember_analysis(OWNER, RISK, _response(age=timedelta(hours=2)))

    _use_settings(monkeypatch, analysis_cache_ttl_seconds=3600)
    assert await cached_analysis(OWNER, "AAPL", RISK) is None

    _use_settings(monkeypatch, analysis_cache_ttl_seconds=3 * 3600)
    assert await cached_analysis(OWNER, "AAPL", RISK) is not None


async def test_default_ttl_reuses_reports_from_the_same_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """By default a report is reused for 24 hours."""
    _use_settings(monkeypatch)
    await remember_analysis(OWNER, RISK, _response(age=timedelta(hours=23)))

    assert await cached_analysis(OWNER, "AAPL", RISK) is not None


async def test_newest_matching_report_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    """When several reports match, the latest one is returned."""
    _use_settings(monkeypatch)
    await remember_analysis(OWNER, RISK, _response(age=timedelta(hours=3), headline="Old."))
    await remember_analysis(OWNER, RISK, _response(age=timedelta(hours=1), headline="New."))
    await remember_analysis(OWNER, RISK, _response(age=timedelta(hours=2), headline="Middle."))

    reused = await cached_analysis(OWNER, "AAPL", RISK)

    assert reused is not None and reused.report.headline == "New."


async def test_refresh_skips_the_stored_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """``refresh`` asks for a new report even when one is stored."""
    _use_settings(monkeypatch)
    await remember_analysis(OWNER, RISK, _response())

    assert await cached_analysis(OWNER, "AAPL", RISK.model_copy(update={"refresh": True})) is None


async def test_zero_ttl_stores_but_never_reuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a zero TTL, reports are still kept as history but never reused."""
    _use_settings(monkeypatch, analysis_cache_ttl_seconds=0)

    saved = await remember_analysis(OWNER, RISK, _response())

    assert saved.id is not None
    assert await cached_analysis(OWNER, "AAPL", RISK) is None


@pytest.mark.parametrize(
    ("request_change", "settings_change"),
    [
        ({"kind": "thesis"}, {}),
        ({"period": "5y"}, {}),
        ({"include_filings": False}, {}),
        ({}, {"anthropic_model": "claude-sonnet-5-5"}),
        ({}, {"anthropic_effort": "high"}),
    ],
)
async def test_reports_are_reused_only_for_identical_requests(
    monkeypatch: pytest.MonkeyPatch,
    request_change: dict[str, object],
    settings_change: dict[str, object],
) -> None:
    """Any option, model or effort that changes the report needs a new one."""
    _use_settings(monkeypatch, anthropic_model="claude-opus-5-5", anthropic_effort="medium")
    await remember_analysis(OWNER, RISK, _response())

    _use_settings(
        monkeypatch,
        **{"anthropic_model": "claude-opus-5-5", "anthropic_effort": "medium", **settings_change},
    )

    assert await cached_analysis(OWNER, "AAPL", RISK.model_copy(update=request_change)) is None
    assert await cached_analysis(OWNER, "MSFT", RISK) is None


async def test_database_failure_writes_a_new_report_unsaved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database outage reads as nothing to reuse, and the new report is returned unsaved."""
    _use_settings(monkeypatch)
    monkeypatch.setattr(analysis_cache, "find_recent_analysis", _database_down)
    monkeypatch.setattr(analysis_cache, "save_analysis", _database_down)
    response = _response()

    assert await cached_analysis(OWNER, "AAPL", RISK) is None
    assert await remember_analysis(OWNER, RISK, response) == response


async def test_unreadable_stored_report_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored report that no longer fits the schema is not reused."""
    _use_settings(monkeypatch)
    saved = await remember_analysis(OWNER, RISK, _response())
    async with get_sessionmaker()() as session:
        record = await session.get(AnalysisRecord, str(saved.id))
        assert record is not None
        record.result = {"symbol": "AAPL"}
        await session.commit()

    assert await cached_analysis(OWNER, "AAPL", RISK) is None


async def test_concurrent_identical_requests_take_turns() -> None:
    """A second identical request waits for the first; a different one does not."""
    order: list[str] = []
    first_inside = asyncio.Event()
    release_first = asyncio.Event()

    async def first() -> None:
        async with analysis_slot(OWNER, "AAPL", RISK):
            order.append("first in")
            first_inside.set()
            await release_first.wait()
            order.append("first out")

    async def duplicate() -> None:
        await first_inside.wait()
        async with analysis_slot(OWNER, "aapl", RISK):
            order.append("duplicate in")

    async def other() -> None:
        await first_inside.wait()
        async with analysis_slot(OWNER, "MSFT", RISK):
            order.append("other in")
        release_first.set()

    await asyncio.gather(first(), duplicate(), other())

    assert order == ["first in", "other in", "first out", "duplicate in"]
    assert analysis_cache._slots == {}


async def test_slot_is_released_when_writing_fails() -> None:
    """An error while holding the slot frees it for the next request."""
    with pytest.raises(RuntimeError):
        async with analysis_slot(OWNER, "AAPL", RISK):
            raise RuntimeError("Claude is overloaded.")

    assert analysis_cache._slots == {}
    async with analysis_slot(OWNER, "AAPL", RISK):
        pass


async def test_stored_analyses_are_listed_and_read_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """History lists headlines newest first, filters, pages, and returns reports in full."""
    _use_settings(monkeypatch)
    old = await remember_analysis(OWNER, RISK, _response(age=timedelta(hours=2), headline="Old."))
    new = await remember_analysis(OWNER, RISK, _response(headline="New."))
    other = await remember_analysis(OWNER, RISK, _response("MSFT", age=timedelta(hours=1)))

    async with get_sessionmaker()() as session:
        everything = await list_analyses(session, OWNER)
        aapl = await list_analyses(session, OWNER, symbol="aapl", limit=1)
        theses = await list_analyses(session, OWNER, kind="thesis")
        full = await get_analysis(session, OWNER, old.id)  # type: ignore[arg-type]
        missing = await get_analysis(session, OWNER, uuid4())

    assert [item.id for item in everything.items] == [new.id, other.id, old.id]
    assert everything.items[0].headline == "New."
    assert everything.items[0].generated_at == new.generated_at
    assert aapl.total == 2 and [item.id for item in aapl.items] == [new.id]
    assert theses.total == 0
    assert full == old
    assert missing is None


async def test_reports_from_an_older_context_are_not_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A report written from older, thinner context data is kept but not reused.

    Reports stored before factor exposures were added are version 1; reusing them would
    serve reports written without factors.
    """
    settings = _use_settings(monkeypatch)
    async with get_sessionmaker()() as session:
        old = await save_analysis(
            session,
            OWNER,
            RISK,
            _response(),
            settings.anthropic_model,
            settings.anthropic_effort,
            ANALYSIS_CONTEXT_VERSION - 1,
        )

    assert await cached_analysis(OWNER, "AAPL", RISK) is None
    async with get_sessionmaker()() as session:
        assert await get_analysis(session, OWNER, old.id) is not None  # type: ignore[arg-type]

    current = await remember_analysis(OWNER, RISK, _response())
    reused = await cached_analysis(OWNER, "AAPL", RISK)
    assert reused is not None and reused.id == current.id
