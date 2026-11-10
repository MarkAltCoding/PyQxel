"""Let a nightly refresh rebuild chosen sets of securities and tickers a user adds.

``nightly_refreshes.universes`` lists the sets each refresh rebuilds, combined: every US
stock and every US ETF, as every refresh did before, or any of the stocks listed on Nasdaq,
the companies in the S&P 500 or the Nasdaq-100, and foreign companies' stocks listed in
the US. ``custom_symbols`` lists tickers it adds to them, and ``user_screener_runs.notes``
what a run could not do as asked.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the columns; refreshes already on keep rebuilding every US stock and ETF."""
    with op.batch_alter_table("nightly_refreshes") as batch:
        batch.add_column(
            sa.Column(
                "universes",
                sa.JSON(),
                server_default='["us_stocks", "us_etfs"]',
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column("custom_symbols", sa.JSON(), server_default="[]", nullable=False)
        )
    with op.batch_alter_table("user_screener_runs") as batch:
        batch.add_column(sa.Column("notes", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Drop the columns; every refresh rebuilds every US stock and ETF again."""
    with op.batch_alter_table("user_screener_runs") as batch:
        batch.drop_column("notes")
    with op.batch_alter_table("nightly_refreshes") as batch:
        batch.drop_column("custom_symbols")
        batch.drop_column("universes")
