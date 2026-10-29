"""Let the admin account turn the yfinance fallback on for itself.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the switch to ``users``, off for every account."""
    op.add_column(
        "users",
        sa.Column("yfinance_fallback", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    """Drop the switch."""
    op.drop_column("users", "yfinance_fallback")
