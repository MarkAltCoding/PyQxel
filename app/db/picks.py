"""Storage of Claude's picks."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import PicksRecord, as_utc
from app.models.picks import PicksList, PicksResponse, PicksSummary


def _response(record: PicksRecord) -> PicksResponse:
    return PicksResponse.model_validate({**record.result, "id": record.id})


async def save_picks(
    session: AsyncSession,
    user_id: str,
    key: str,
    response: PicksResponse,
    requested_model: str,
    effort: str,
) -> PicksResponse:
    """Store ``response``, made for the request ``key`` of ``user_id``; return it with its
    ID.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: If the database cannot be written.
    """
    saved = response.model_copy(update={"id": uuid4(), "cached": False})
    profile = saved.context.profile
    session.add(
        PicksRecord(
            id=str(saved.id),
            user_id=user_id,
            created_at=saved.generated_at,
            request_key=key,
            requested_model=requested_model,
            effort=effort,
            model=saved.model,
            horizon=saved.context.horizon[:80],
            risk_tolerance=profile.risk_tolerance,
            headline=saved.report.headline,
            symbols=[pick.symbol for pick in saved.report.picks],
            result=saved.model_dump(mode="json", exclude={"id"}),
        )
    )
    await session.commit()
    return saved


async def find_recent_picks(
    session: AsyncSession,
    user_id: str,
    key: str,
    requested_model: str,
    effort: str,
    since: datetime,
) -> PicksResponse | None:
    """The account's newest picks since ``since`` for the same request and settings."""
    record = await session.scalar(
        select(PicksRecord)
        .where(
            PicksRecord.user_id == user_id,
            PicksRecord.request_key == key,
            PicksRecord.requested_model == requested_model,
            PicksRecord.effort == effort,
            PicksRecord.created_at >= since,
        )
        .order_by(PicksRecord.created_at.desc())
        .limit(1)
    )
    return None if record is None else _response(record)


async def latest_picks(session: AsyncSession, user_id: str) -> PicksResponse | None:
    """The account's newest picks, whatever they were made for."""
    record = await session.scalar(
        select(PicksRecord)
        .where(PicksRecord.user_id == user_id)
        .order_by(PicksRecord.created_at.desc())
        .limit(1)
    )
    return None if record is None else _response(record)


async def get_picks(session: AsyncSession, user_id: str, picks_id: UUID) -> PicksResponse | None:
    """``user_id``'s stored picks with ``picks_id``, or ``None`` if they have none."""
    record = await session.get(PicksRecord, str(picks_id))
    return None if record is None or record.user_id != user_id else _response(record)


async def list_picks(session: AsyncSession, user_id: str, limit: int = 20) -> PicksList:
    """The account's stored lists of picks, newest first, without their reasons."""
    records = await session.scalars(
        select(PicksRecord)
        .where(PicksRecord.user_id == user_id)
        .order_by(PicksRecord.created_at.desc(), PicksRecord.id)
        .limit(limit)
    )
    return PicksList(
        items=[
            PicksSummary(
                id=UUID(record.id),
                generated_at=as_utc(record.created_at),
                horizon=record.horizon,
                risk_tolerance=record.risk_tolerance,
                model=record.model,
                headline=record.headline,
                symbols=list(record.symbols),
            )
            for record in records
        ]
    )


async def delete_picks(session: AsyncSession, user_id: str, picks_id: UUID) -> bool:
    """Delete ``user_id``'s stored picks with ``picks_id``; return whether they existed."""
    record = await session.get(PicksRecord, str(picks_id))
    if record is None or record.user_id != user_id:
        return False
    await session.delete(record)
    await session.commit()
    return True
