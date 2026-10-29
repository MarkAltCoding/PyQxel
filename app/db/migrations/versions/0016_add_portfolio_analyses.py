"""Store AI reviews of saved portfolios, and count coding assistant answers.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``portfolio_analyses`` and ``assistant_requests``."""
    op.create_table(
        "portfolio_analyses",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("holdings_key", sa.String(length=64), nullable=False),
        sa.Column("requested_model", sa.String(length=64), nullable=False),
        sa.Column("effort", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
    )
    op.create_index("ix_portfolio_analyses_user_id", "portfolio_analyses", ["user_id"])
    op.create_index(
        "ix_portfolio_analyses_user_created_at", "portfolio_analyses", ["user_id", "created_at"]
    )
    op.create_table(
        "assistant_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
    )
    op.create_index("ix_assistant_requests_user_id", "assistant_requests", ["user_id"])
    op.create_index(
        "ix_assistant_requests_user_created_at", "assistant_requests", ["user_id", "created_at"]
    )


def downgrade() -> None:
    """Drop both tables."""
    op.drop_table("assistant_requests")
    op.drop_table("portfolio_analyses")
