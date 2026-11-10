"""Let a nightly refresh rebuild a smaller universe.

``nightly_refreshes.universe`` says what each refresh rebuilds: every US stock and ETF, as
every refresh did before, the stocks alone, those listed on Nasdaq, or the companies in the
S&P 500 or the Nasdaq-100.

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
    """Add the universe column; refreshes already on keep rebuilding everything."""
    with op.batch_alter_table("nightly_refreshes") as batch:
        batch.add_column(
            sa.Column("universe", sa.String(length=16), server_default="all", nullable=False)
        )


def downgrade() -> None:
    """Drop the column; every refresh rebuilds everything again."""
    with op.batch_alter_table("nightly_refreshes") as batch:
        batch.drop_column("universe")
