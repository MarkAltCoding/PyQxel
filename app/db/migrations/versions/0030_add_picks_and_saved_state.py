"""Store Claude's picks, and what an account enters in the app.

``claude_picks`` keeps each list of picks Claude made for an account. ``saved_state``
keeps what an account typed or chose in the app that used to live only in one browser
(strategy drafts, screens, the answers given for a review, display choices), so it
follows the account to every device and through every update.

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create both tables, empty."""
    op.create_table(
        "claude_picks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_key", sa.String(length=64), nullable=False),
        sa.Column("requested_model", sa.String(length=64), nullable=False),
        sa.Column("effort", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("horizon", sa.String(length=80), nullable=False),
        sa.Column("risk_tolerance", sa.String(length=16), nullable=True),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("symbols", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
    )
    op.create_index("ix_claude_picks_user_id", "claude_picks", ["user_id"])
    op.create_index("ix_claude_picks_user_created_at", "claude_picks", ["user_id", "created_at"])
    op.create_table(
        "saved_state",
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    """Drop both tables, and with them the picks and saved state."""
    op.drop_table("saved_state")
    op.drop_index("ix_claude_picks_user_created_at", table_name="claude_picks")
    op.drop_index("ix_claude_picks_user_id", table_name="claude_picks")
    op.drop_table("claude_picks")
