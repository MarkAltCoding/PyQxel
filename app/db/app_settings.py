"""Settings of the whole server, changed by its admin.

One is kept so far: whether the server uses data sources whose terms do not clearly allow
use in a paid product (:data:`RESTRICTED_SOURCES`). It is on by default, as before it could
be turned off. Requests read it often, so it is kept in memory for
:data:`MEMORY_SECONDS`; a change by the admin applies at once in the process that made it,
and within that time in the others.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.credentials import current_provider_keys
from app.db.session import get_sessionmaker
from app.db.tables import AppSettingRecord

logger = logging.getLogger(__name__)

RESTRICTED_SOURCES: str = "restricted_sources"
"""Whether data sources not cleared for commercial use are used."""

RESTRICTED_SOURCE_USES: tuple[str, ...] = (
    "Ken French's data library (copyrighted, no license stated): the Fama-French and Carhart "
    "factor models on the Stock, Backtest and Portfolio pages and in AI reports, the "
    "screener's factor betas, and the interest cash earns in backtests before 2002",
)
"""What turning :data:`RESTRICTED_SOURCES` off stops, for the admin to read."""

MEMORY_SECONDS: float = 30.0
"""How long a setting read from the database is reused in this process."""

_memory: dict[str, tuple[float, Any]] = {}
"""Setting name -> (time read, value)."""


async def _read(name: str, default: Any) -> Any:
    """A setting's value, from memory or the database; ``default`` when it was never set,
    and the last value known when the database cannot be read."""
    remembered = _memory.get(name)
    if remembered is not None and time.monotonic() - remembered[0] < MEMORY_SECONDS:
        return remembered[1]
    try:
        async with get_sessionmaker()() as session:
            record = await session.get(AppSettingRecord, name)
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("The %s setting could not be read: %s", name, exc)
        return default if remembered is None else remembered[1]
    value = default if record is None else record.value
    _memory[name] = (time.monotonic(), value)
    return value


async def server_uses_restricted_sources() -> bool:
    """Whether the server uses data sources not cleared for commercial use, for every
    account."""
    return bool(await _read(RESTRICTED_SOURCES, True))


async def restricted_sources_allowed() -> bool:
    """Whether the current request uses data sources not cleared for commercial use: when
    the server does, or when it is the admin's own and their switch is on."""
    return current_provider_keys().restricted_sources or await server_uses_restricted_sources()


async def set_setting(session: AsyncSession, name: str, value: Any) -> None:
    """Store a setting's value, applying it at once in this process."""
    record = await session.get(AppSettingRecord, name)
    now = datetime.now(timezone.utc)
    if record is None:
        session.add(AppSettingRecord(name=name, value=value, updated_at=now))
    else:
        record.value = value
        record.updated_at = now
    await session.commit()
    _memory[name] = (time.monotonic(), value)


def clear_memory() -> None:
    """Forget the settings kept in memory."""
    _memory.clear()
