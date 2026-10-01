"""Alembic environment for the results database.

The app runs migrations at startup through :func:`app.db.session.init_db`, which
hands over an open connection. From the command line (``alembic upgrade head``,
``alembic revision --autogenerate -m "..."``) the database comes from ``DATABASE_URL``.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.db.session import create_engine_for
from app.db.tables import Base

target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    """Run the pending migrations on ``connection``."""
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
