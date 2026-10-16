"""Let accounts store an Alpha Vantage key.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the encrypted key and its hint to ``users``."""
    op.add_column("users", sa.Column("alpha_vantage_api_key", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("alpha_vantage_key_hint", sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Drop the Alpha Vantage key columns."""
    op.drop_column("users", "alpha_vantage_key_hint")
    op.drop_column("users", "alpha_vantage_api_key")
