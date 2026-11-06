"""Add settings of the whole server, changed by its admin.

``app_settings`` holds named JSON values, such as whether the server uses data sources
not cleared for commercial use, and ``users.own_restricted_sources`` whether the admin's
own requests use them while the server does not.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the settings table."""
    op.create_table(
        "app_settings",
        sa.Column("name", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "own_restricted_sources", sa.Boolean(), server_default=sa.false(), nullable=False
            )
        )


def downgrade() -> None:
    """Drop the settings table and the admin's own switch; every setting returns to its
    default."""
    with op.batch_alter_table("users") as batch:
        batch.drop_column("own_restricted_sources")
    op.drop_table("app_settings")
