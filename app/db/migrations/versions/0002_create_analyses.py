"""Create the analyses table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``analyses`` and the index used to find a reusable report."""
    op.create_table(
        "analyses",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("include_filings", sa.Boolean(), nullable=False),
        sa.Column("requested_model", sa.String(length=64), nullable=False),
        sa.Column("effort", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
    )
    op.create_index("ix_analyses_created_at", "analyses", ["created_at"])
    op.create_index(
        "ix_analyses_reuse",
        "analyses",
        [
            "symbol",
            "kind",
            "period",
            "include_filings",
            "requested_model",
            "effort",
            "created_at",
        ],
    )


def downgrade() -> None:
    """Drop ``analyses``."""
    op.drop_index("ix_analyses_reuse", table_name="analyses")
    op.drop_index("ix_analyses_created_at", table_name="analyses")
    op.drop_table("analyses")
