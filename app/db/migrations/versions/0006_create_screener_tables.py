"""Create the screener tables: screened stocks, their fundamentals, and refresh runs.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

METRICS: tuple[str, ...] = (
    "pe_ratio",
    "price_to_sales",
    "ev_to_ebitda",
    "fcf_yield",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "return_on_equity",
    "revenue_growth",
    "earnings_growth",
    "return_1m",
    "return_6m",
    "momentum_12_1",
    "volatility",
    "max_drawdown",
    "beta_market",
    "beta_size",
    "beta_value",
    "beta_momentum",
    "factor_r_squared",
)
"""Metric columns that may be null."""


def upgrade() -> None:
    """Create ``screener_stocks``, ``screener_fundamentals`` and ``screener_runs``."""
    op.create_table(
        "screener_stocks",
        sa.Column("symbol", sa.String(length=16), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("exchange", sa.String(length=16), nullable=False),
        sa.Column("sector", sa.String(length=64), nullable=True),
        sa.Column("industry", sa.String(length=128), nullable=True),
        sa.Column("cik", sa.Integer(), nullable=False),
        sa.Column("market_cap_source", sa.String(length=16), nullable=False),
        sa.Column("market_cap_rank", sa.Integer(), nullable=False),
        sa.Column("fundamentals_period_end", sa.Date(), nullable=True),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("market_cap", sa.Float(), nullable=False),
        sa.Column("avg_dollar_volume", sa.Float(), nullable=False),
        *(sa.Column(name, sa.Float(), nullable=True) for name in METRICS),
    )
    op.create_index("ix_screener_stocks_cik", "screener_stocks", ["cik"])
    op.create_index("ix_screener_stocks_sector", "screener_stocks", ["sector"])
    op.create_index("ix_screener_stocks_market_cap_rank", "screener_stocks", ["market_cap_rank"])
    op.create_table(
        "screener_fundamentals",
        sa.Column("cik", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_period_end", sa.Date(), nullable=True),
        sa.Column("financials", sa.JSON(), nullable=False),
    )
    op.create_table(
        "screener_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stocks", sa.Integer(), nullable=True),
        sa.Column("prices_as_of", sa.Date(), nullable=True),
        sa.Column(
            "fundamentals_refreshed", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("factor_data_end", sa.Date(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_screener_runs_started_at", "screener_runs", ["started_at"])


def downgrade() -> None:
    """Drop the screener tables."""
    op.drop_index("ix_screener_runs_started_at", table_name="screener_runs")
    op.drop_table("screener_runs")
    op.drop_table("screener_fundamentals")
    op.drop_index("ix_screener_stocks_market_cap_rank", table_name="screener_stocks")
    op.drop_index("ix_screener_stocks_sector", table_name="screener_stocks")
    op.drop_index("ix_screener_stocks_cik", table_name="screener_stocks")
    op.drop_table("screener_stocks")
