"""Create the backtests table.

Revision ID: 0001
Revises:
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``backtests`` and its listing indexes."""
    op.create_table(
        "backtests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("strategy", sa.String(length=32), nullable=False),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("interval", sa.String(length=8), nullable=False),
        sa.Column("total_return", sa.Float(), nullable=False),
        sa.Column("sharpe_ratio", sa.Float(), nullable=True),
        sa.Column("max_drawdown", sa.Float(), nullable=False),
        sa.Column("benchmark_total_return", sa.Float(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
    )
    op.create_index("ix_backtests_created_at", "backtests", ["created_at"])
    op.create_index("ix_backtests_symbol_created_at", "backtests", ["symbol", "created_at"])


def downgrade() -> None:
    """Drop ``backtests``."""
    op.drop_index("ix_backtests_symbol_created_at", table_name="backtests")
    op.drop_index("ix_backtests_created_at", table_name="backtests")
    op.drop_table("backtests")
