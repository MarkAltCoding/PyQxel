"""Reuse of finished AI analyses, which are slow and billed per request.

Every report is stored in the results database. A later request for the same symbol
and options, under the same configured model and effort, gets the stored report back
for ``ANALYSIS_CACHE_TTL_SECONDS`` instead of a new, billed one. Within one process,
identical requests that arrive together are written once: the later ones wait in
:func:`analysis_slot` and then reuse the first one's report.

A database failure never blocks an analysis: lookups then miss and reports go unsaved.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.analyses import find_recent_analysis, save_analysis
from app.db.session import get_sessionmaker
from app.models.research import AnalysisRequest, AnalysisResponse

logger = logging.getLogger(__name__)

_slots: dict[tuple[object, ...], tuple[asyncio.Lock, int]] = {}
"""Lock and number of holders or waiters for each request being written."""


def _slot_key(symbol: str, request: AnalysisRequest) -> tuple[object, ...]:
    """Identify the requests that would get the same report under the current settings."""
    settings = get_settings()
    return (
        symbol.upper(),
        request.kind,
        request.period,
        request.include_filings,
        settings.anthropic_model,
        settings.anthropic_effort,
    )


async def cached_analysis(symbol: str, request: AnalysisRequest) -> AnalysisResponse | None:
    """Return a recent report for the same request, or ``None`` if there is none to reuse.

    Requests with ``refresh`` set, and settings with a zero TTL, never reuse a report.
    """
    settings = get_settings()
    if request.refresh or settings.analysis_cache_ttl_seconds == 0:
        return None
    since = datetime.now(timezone.utc) - timedelta(seconds=settings.analysis_cache_ttl_seconds)
    try:
        async with get_sessionmaker()() as session:
            found = await find_recent_analysis(
                session,
                symbol,
                request,
                settings.anthropic_model,
                settings.anthropic_effort,
                since,
            )
    except SQLAlchemyError as exc:
        logger.error("Could not look up a stored %s analysis: %s", symbol, exc)
        return None
    except ValueError:
        logger.warning("Ignoring an unreadable stored analysis for %s.", symbol)
        return None
    return None if found is None else found.model_copy(update={"cached": True})


async def remember_analysis(
    request: AnalysisRequest, response: AnalysisResponse
) -> AnalysisResponse:
    """Store a newly written ``response``; return it with its ID, or unchanged if unsaved."""
    settings = get_settings()
    try:
        async with get_sessionmaker()() as session:
            return await save_analysis(
                session, request, response, settings.anthropic_model, settings.anthropic_effort
            )
    except SQLAlchemyError as exc:
        logger.error("Could not store the %s analysis: %s", response.symbol, exc)
        return response


@asynccontextmanager
async def analysis_slot(symbol: str, request: AnalysisRequest) -> AsyncIterator[None]:
    """Hold the right to write the report for this request, waiting for any writer before.

    Callers should look for a cached report again once inside, since the writer they
    waited for has usually just stored one.
    """
    key = _slot_key(symbol, request)
    lock, users = _slots.get(key, (asyncio.Lock(), 0))
    _slots[key] = (lock, users + 1)
    try:
        async with lock:
            yield
    finally:
        lock, users = _slots[key]
        if users == 1:
            del _slots[key]
        else:
            _slots[key] = (lock, users - 1)
