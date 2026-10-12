"""Create the holdings of each account's saved portfolio.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``holdings``."""
    op.create_table(
        "holdings",
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("symbol", sa.String(length=16), primary_key=True),
        sa.Column("shares", sa.Float(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    """Drop ``holdings``."""
    op.drop_table("holdings")
