"""Add the sign-ins the native app collects from the system browser.

``app_sign_ins`` holds, for a few minutes, a sign-in finished in the system browser for
the native app, keyed by the hash of the one-time code the browser hands the app.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the table of sign-ins waiting for the app."""
    op.create_table(
        "app_sign_ins",
        sa.Column("code_hash", sa.String(length=64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("challenge", sa.String(length=64), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_app_sign_ins_user_id", "app_sign_ins", ["user_id"])


def downgrade() -> None:
    """Drop the table; sign-ins waiting for the app are lost, and must be started again."""
    op.drop_index("ix_app_sign_ins_user_id", table_name="app_sign_ins")
    op.drop_table("app_sign_ins")
