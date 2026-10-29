"""Settings for the whole server, which the admin changes from their Settings page.

Every request reads them, so they are kept in memory for a few seconds; a change made
on one instance reaches the others within :data:`CACHE_SECONDS`.
"""

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import AppSettingsRecord

CACHE_SECONDS: float = 10.0

ROW_ID: int = 1

_cached: tuple[float, bool] | None = None


def clear_app_settings_memory() -> None:
    """Forget the cached settings, so the next read goes to the database."""
    global _cached
    _cached = None


async def yfinance_for_everyone(session: AsyncSession) -> bool:
    """Whether the admin turned the yfinance fallback on for every account."""
    global _cached
    if _cached is not None and time.monotonic() - _cached[0] <= CACHE_SECONDS:
        return _cached[1]
    record = await session.get(AppSettingsRecord, ROW_ID)
    enabled = bool(record is not None and record.yfinance_fallback)
    _cached = (time.monotonic(), enabled)
    return enabled


async def set_yfinance_for_everyone(session: AsyncSession, enabled: bool) -> None:
    """Turn the yfinance fallback on or off for every account and the screener."""
    global _cached
    record = await session.get(AppSettingsRecord, ROW_ID)
    if record is None:
        session.add(AppSettingsRecord(id=ROW_ID, yfinance_fallback=enabled))
    else:
        record.yfinance_fallback = enabled
    await session.commit()
    _cached = (time.monotonic(), enabled)
