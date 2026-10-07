"""Create user accounts and API keys, and give stored results an owner.

Existing results have no owner until ``python -m app.jobs.users create --claim-unowned``
assigns them to an account.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNED: tuple[str, ...] = ("backtests", "analyses", "simulations")
"""Tables of stored results, each of which gains a ``user_id``."""


def upgrade() -> None:
    """Create ``users`` and ``api_keys`` and add ``user_id`` to stored results."""
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("anthropic_api_key", sa.Text(), nullable=True),
        sa.Column("anthropic_key_hint", sa.String(length=16), nullable=True),
        sa.Column("fmp_api_key", sa.Text(), nullable=True),
        sa.Column("fmp_key_hint", sa.String(length=16), nullable=True),
        sa.Column("ai_requests_per_hour", sa.Integer(), nullable=True),
        sa.Column("ai_requests_per_day", sa.Integer(), nullable=True),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    for table in OWNED:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("user_id", sa.String(length=36), nullable=True))
            batch.create_index(f"ix_{table}_user_id", ["user_id"])
            batch.create_foreign_key(
                f"fk_{table}_user_id_users", "users", ["user_id"], ["id"], ondelete="CASCADE"
            )


def downgrade() -> None:
    """Drop the owners of stored results, ``api_keys`` and ``users``."""
    for table in OWNED:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_user_id_users", type_="foreignkey")
            batch.drop_index(f"ix_{table}_user_id")
            batch.drop_column("user_id")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("users")
