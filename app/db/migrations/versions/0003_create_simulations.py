"""Create the simulations table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``simulations`` and its listing index."""
    op.create_table(
        "simulations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbols", sa.Text(), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False),
        sa.Column("paths", sa.Integer(), nullable=False),
        sa.Column("dependence", sa.String(length=16), nullable=False),
        sa.Column("marginals", sa.String(length=16), nullable=False),
        sa.Column("expected_return", sa.Float(), nullable=False),
        sa.Column("probability_of_loss", sa.Float(), nullable=False),
        sa.Column("value_at_risk_95", sa.Float(), nullable=False),
        sa.Column("conditional_value_at_risk_95", sa.Float(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
    )
    op.create_index("ix_simulations_created_at", "simulations", ["created_at"])


def downgrade() -> None:
    """Drop ``simulations``."""
    op.drop_index("ix_simulations_created_at", table_name="simulations")
    op.drop_table("simulations")
