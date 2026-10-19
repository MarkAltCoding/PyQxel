"""Let people sign in with GitHub or Google.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the identities table, and mark the API keys that signing in issues."""
    op.create_table(
        "identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "subject", name="uq_identities_provider"),
    )
    op.create_index("ix_identities_user_id", "identities", ["user_id"])
    op.add_column("api_keys", sa.Column("signed_in_with", sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Drop the identities table and the sign-in marker."""
    with op.batch_alter_table("api_keys") as batch:
        batch.drop_column("signed_in_with")
    op.drop_index("ix_identities_user_id", table_name="identities")
    op.drop_table("identities")
