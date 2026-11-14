"""Storage of what an account enters in the app (see :mod:`app.models.saved_state`)."""

from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import SavedStateRecord
from app.models.saved_state import MAX_SAVED_KEYS, MAX_SAVED_TOTAL_LENGTH


class SavedStateFullError(ValueError):
    """Raised when saving would take an account past what it may keep."""


async def load_saved_state(session: AsyncSession, user_id: str) -> dict[str, str]:
    """Every value ``user_id`` has saved, by name."""
    records = await session.scalars(
        select(SavedStateRecord).where(SavedStateRecord.user_id == user_id)
    )
    return {record.key: record.value for record in records}


async def update_saved_state(
    session: AsyncSession, user_id: str, items: dict[str, str | None]
) -> None:
    """Save each named value of ``items`` for ``user_id``, replacing what was there, and
    forget those given as ``None``.

    Raises:
        SavedStateFullError: If the account would then hold more values, or more text,
            than it may; nothing is changed.
    """
    removed = [key for key, value in items.items() if value is None]
    kept = {key: value for key, value in items.items() if value is not None}
    others = (
        await session.execute(
            select(
                func.count(), func.coalesce(func.sum(func.length(SavedStateRecord.value)), 0)
            ).where(SavedStateRecord.user_id == user_id, SavedStateRecord.key.not_in(list(items)))
        )
    ).one()
    count, length = int(others[0]), int(others[1])
    if count + len(kept) > MAX_SAVED_KEYS:
        raise SavedStateFullError(f"An account may keep at most {MAX_SAVED_KEYS} saved values.")
    if length + sum(len(value) for value in kept.values()) > MAX_SAVED_TOTAL_LENGTH:
        raise SavedStateFullError("The account's saved values would take too much room.")
    if removed:
        await session.execute(
            delete(SavedStateRecord).where(
                SavedStateRecord.user_id == user_id, SavedStateRecord.key.in_(removed)
            )
        )
    now = datetime.now(timezone.utc)
    for key, value in kept.items():
        await session.merge(SavedStateRecord(user_id=user_id, key=key, value=value, updated_at=now))
    await session.commit()
