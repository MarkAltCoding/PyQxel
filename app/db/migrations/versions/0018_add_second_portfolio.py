"""Let each account save two portfolios, and name them.

Holdings move to ``portfolio_holdings``, keyed by portfolio as well as symbol, so the
same symbol can be in both; the existing holdings become portfolio 1. Reviews record
which portfolio they are of.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _user_id() -> sa.Column[str]:
    return sa.Column(
        "user_id",
        sa.String(length=36),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )


def upgrade() -> None:
    """Create ``portfolios`` and ``portfolio_holdings``, moving ``holdings`` into it."""
    op.create_table(
        "portfolios",
        _user_id(),
        sa.Column("portfolio", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=40), nullable=False),
    )
    op.create_table(
        "portfolio_holdings",
        _user_id(),
        sa.Column("portfolio", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(length=16), primary_key=True),
        sa.Column("shares", sa.Float(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
    )
    op.execute(
        "INSERT INTO portfolio_holdings (user_id, portfolio, symbol, shares, position) "
        "SELECT user_id, 1, symbol, shares, position FROM holdings"
    )
    op.drop_table("holdings")
    with op.batch_alter_table("portfolio_analyses") as batch:
        batch.add_column(sa.Column("portfolio", sa.Integer(), server_default="1", nullable=False))


def downgrade() -> None:
    """Keep only portfolio 1, in ``holdings``; drop the names and the reviews' portfolio."""
    with op.batch_alter_table("portfolio_analyses") as batch:
        batch.drop_column("portfolio")
    op.create_table(
        "holdings",
        _user_id(),
        sa.Column("symbol", sa.String(length=16), primary_key=True),
        sa.Column("shares", sa.Float(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
    )
    op.execute(
        "INSERT INTO holdings (user_id, symbol, shares, position) "
        "SELECT user_id, symbol, shares, position FROM portfolio_holdings WHERE portfolio = 1"
    )
    op.drop_table("portfolio_holdings")
    op.drop_table("portfolios")
