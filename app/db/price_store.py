"""Each user's stored daily price history, fetched on their own keys and kept for them alone.

A page or backtest asking for a symbol's daily bars is served from here when the stored
bars are fresh and reach back far enough, so it costs no request on the user's plan; when
they are stale, only the days since are fetched. Rows hold a frame as compressed JSON
(serialized by :mod:`app.data.fetcher`); this module only stores and reads them.

The providers' terms, as ``docs/data-sources.md`` records them, allow keeping data for the
key owner's own personal use but not showing it to anyone else, so rows are keyed by user
and read only for that user's requests, and only while they send a key for the provider
that served them. Rows unread for :data:`UNREAD_DAYS` are deleted, and so is everything
when the user deletes the account or chooses to in Settings.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import StoredHistoryRecord, as_utc

logger = logging.getLogger(__name__)

UNREAD_DAYS: int = 30
"""Days a stored history is kept without being read."""


@dataclass(frozen=True)
class StoredHistory:
    """A stored history and what is known about it."""

    provider: str
    symbol: str
    requested_from: date
    first_day: date
    last_day: date
    bars: int
    notice: str | None
    fetched_at: datetime
    data: bytes


@dataclass(frozen=True)
class StoreSummary:
    """How much price history is stored for a user."""

    symbols: int
    histories: int
    bytes: int
    oldest_fetch: datetime | None


def _from_record(record: StoredHistoryRecord) -> StoredHistory:
    return StoredHistory(
        provider=record.provider,
        symbol=record.symbol,
        requested_from=record.requested_from,
        first_day=record.first_day,
        last_day=record.last_day,
        bars=record.bars,
        notice=record.notice,
        fetched_at=as_utc(record.fetched_at),
        data=record.data,
    )


async def load_histories(
    session: AsyncSession, user_id: str, symbol: str, providers: Sequence[str]
) -> dict[str, StoredHistory]:
    """``user_id``'s stored histories of ``symbol`` from each of ``providers``."""
    if not providers:
        return {}
    records = await session.scalars(
        select(StoredHistoryRecord).where(
            StoredHistoryRecord.user_id == user_id,
            StoredHistoryRecord.symbol == symbol,
            StoredHistoryRecord.provider.in_(providers),
        )
    )
    return {record.provider: _from_record(record) for record in records}


async def mark_read(session: AsyncSession, user_id: str, provider: str, symbol: str) -> None:
    """Note that a stored history was just used, so it is not deleted as unread."""
    record = await session.get(StoredHistoryRecord, (user_id, provider, symbol))
    if record is not None:
        record.read_at = datetime.now(UTC)
        await session.commit()


async def save_history(session: AsyncSession, user_id: str, history: StoredHistory) -> None:
    """Store ``history`` for ``user_id``, replacing the one from the same provider, and
    delete the user's histories left unread for :data:`UNREAD_DAYS`."""
    now = datetime.now(UTC)
    record = await session.get(StoredHistoryRecord, (user_id, history.provider, history.symbol))
    if record is None:
        record = StoredHistoryRecord(
            user_id=user_id, provider=history.provider, symbol=history.symbol
        )
        session.add(record)
    record.requested_from = history.requested_from
    record.first_day = history.first_day
    record.last_day = history.last_day
    record.bars = history.bars
    record.notice = history.notice
    record.fetched_at = history.fetched_at
    record.read_at = now
    record.data = history.data
    await session.execute(
        delete(StoredHistoryRecord).where(
            StoredHistoryRecord.user_id == user_id,
            StoredHistoryRecord.read_at < now - timedelta(days=UNREAD_DAYS),
        )
    )
    await session.commit()


async def store_summary(session: AsyncSession, user_id: str) -> StoreSummary:
    """How many symbols' histories are stored for ``user_id``, and their size."""
    row = (
        await session.execute(
            select(
                func.count(func.distinct(StoredHistoryRecord.symbol)),
                func.count(),
                func.coalesce(func.sum(func.length(StoredHistoryRecord.data)), 0),
                func.min(StoredHistoryRecord.fetched_at),
            ).where(StoredHistoryRecord.user_id == user_id)
        )
    ).one()
    oldest = row[3]
    return StoreSummary(
        symbols=int(row[0]),
        histories=int(row[1]),
        bytes=int(row[2]),
        oldest_fetch=None if oldest is None else as_utc(oldest),
    )


async def delete_histories(session: AsyncSession, user_id: str) -> int:
    """Delete every history stored for ``user_id``; return how many there were."""
    result = await session.execute(
        delete(StoredHistoryRecord).where(StoredHistoryRecord.user_id == user_id)
    )
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)
