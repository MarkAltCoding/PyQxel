"""Alembic environment for the results database.

The app runs migrations at startup through :func:`app.db.session.init_db`, which
hands over an open connection. From the command line (``alembic upgrade head``,
``alembic revision --autogenerate -m "..."``) the database comes from ``DATABASE_URL``.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.db.session import create_engine_for
from app.db.tables import Base

target_metadata = Base.metadata

MIGRATION_LOCK_ID: int = 0x5079_5178_656C
"""Postgres advisory lock held while migrating ("PyQxel" in hex), so app instances
starting together migrate one at a time instead of racing to create the same tables."""


def _run(connection: Connection) -> None:
    """Run the pending migrations on ``connection``."""
    if connection.dialect.name == "postgresql":
        # Released when the migration transaction ends; later instances then find
        # nothing left to do.
        connection.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": MIGRATION_LOCK_ID})
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite cannot alter most columns in place; batch mode rebuilds the table.
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_from_settings() -> None:
    """Connect to ``DATABASE_URL`` and migrate it."""
    engine = create_engine_for(get_settings().database_url)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
        await connection.commit()
    await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline (--sql) migrations are not supported; connect to a database.")

provided: Connection | None = context.config.attributes.get("connection")
if provided is None:
    if context.config.config_file_name is not None:
        fileConfig(context.config.config_file_name)
    asyncio.run(_run_from_settings())
else:
    _run(provided)
