"""Add the rebalancing column to simulations.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record how each simulation rebalanced; earlier ones all rebalanced daily."""
    with op.batch_alter_table("simulations") as batch:
        batch.add_column(
            sa.Column("rebalancing", sa.String(length=16), nullable=False, server_default="daily")
        )


def downgrade() -> None:
    """Drop the rebalancing column."""
    with op.batch_alter_table("simulations") as batch:
        batch.drop_column("rebalancing")
