"""Let accounts store a Finnhub key.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the encrypted key and its hint to ``users``."""
    op.add_column("users", sa.Column("finnhub_api_key", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("finnhub_key_hint", sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Drop the Finnhub key columns."""
    op.drop_column("users", "finnhub_key_hint")
    op.drop_column("users", "finnhub_api_key")
