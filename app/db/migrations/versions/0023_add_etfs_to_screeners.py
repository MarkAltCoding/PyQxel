"""Let a screened universe hold ETFs beside stocks.

``asset_type`` says whether a row is a company's stock or an exchange-traded fund. An ETF
is not a company, so it has no SEC registrant, market cap or market cap rank, and those
columns become nullable. Every existing row is a stock.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES: tuple[str, ...] = ("screener_stocks", "user_screener_stocks")
"""The shared universe's table and each user's own."""


def upgrade() -> None:
    """Add ``asset_type`` and make the company-only columns nullable."""
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column("asset_type", sa.String(length=8), server_default="stock", nullable=False)
            )
            batch.alter_column("cik", existing_type=sa.Integer(), nullable=True)
            batch.alter_column("market_cap", existing_type=sa.Float(), nullable=True)
            batch.alter_column("market_cap_rank", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Delete the ETF rows, then drop ``asset_type`` and require the company columns."""
    for table in TABLES:
        op.execute(sa.text(f"DELETE FROM {table} WHERE asset_type = 'etf'"))
        with op.batch_alter_table(table) as batch:
            batch.alter_column("market_cap_rank", existing_type=sa.Integer(), nullable=False)
            batch.alter_column("market_cap", existing_type=sa.Float(), nullable=False)
            batch.alter_column("cik", existing_type=sa.Integer(), nullable=False)
            batch.drop_column("asset_type")
