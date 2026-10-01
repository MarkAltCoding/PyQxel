"""Add the context version to stored analyses.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record which version of context data each report was written from.

    Reports stored before this column existed were written without factor exposures,
    so they are version 1 and are no longer reused.
    """
    with op.batch_alter_table("analyses") as batch:
        batch.add_column(
            sa.Column("context_version", sa.Integer(), nullable=False, server_default="1")
        )


def downgrade() -> None:
    """Drop the context version."""
    with op.batch_alter_table("analyses") as batch:
        batch.drop_column("context_version")
