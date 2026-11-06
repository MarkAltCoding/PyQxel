"""Add settings of the whole server, changed by its admin.

``app_settings`` holds named JSON values, such as whether the server uses data sources
not cleared for commercial use.

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


def downgrade() -> None:
    """Drop the settings table; every setting returns to its default."""
    op.drop_table("app_settings")
