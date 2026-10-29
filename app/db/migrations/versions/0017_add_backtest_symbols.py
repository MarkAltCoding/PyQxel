"""Record every symbol of backtests that allocate between several.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``symbols``; earlier backtests each traded one symbol, so it stays null."""
    with op.batch_alter_table("backtests") as batch:
        batch.add_column(sa.Column("symbols", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Drop ``symbols``."""
    with op.batch_alter_table("backtests") as batch:
        batch.drop_column("symbols")
