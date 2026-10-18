"""Store a custom strategy's source and language with its backtest.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the code and language columns; built-in strategies leave them null."""
    op.add_column("backtests", sa.Column("code", sa.Text(), nullable=True))
    op.add_column("backtests", sa.Column("language", sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Drop the code and language columns."""
    with op.batch_alter_table("backtests") as batch:
        batch.drop_column("language")
        batch.drop_column("code")
