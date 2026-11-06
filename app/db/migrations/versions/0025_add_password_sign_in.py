"""Add signing in with an email and password.

``users.password_hash`` holds an account's Argon2id password hash, and ``email_links``
the links emailed to choose a password, which prove the address before an account is
made or a password added to one.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the emailed links' table and the password column."""
    op.create_table(
        "email_links",
        sa.Column("token_hash", sa.String(length=64), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_email_links_email", "email_links", ["email"])
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("password_hash", sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Drop the links and every password; accounts keep their other ways to sign in."""
    with op.batch_alter_table("users") as batch:
        batch.drop_column("password_hash")
    op.drop_index("ix_email_links_email", table_name="email_links")
    op.drop_table("email_links")
