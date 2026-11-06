"""Add each user's own nightly screener refresh.

``screener_listings`` keeps the SEC's list of listed common stocks, and
``screener_companies`` their SEC industry codes, the shared first stage every user's
refresh starts from. ``nightly_refreshes`` holds the opt-ins and the
encrypted keys of the users who chose one, ``user_screener_runs`` each user's refreshes,
and ``user_screener_stocks`` each user's own universe.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
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
"""Metric columns that may be null, as in ``screener_stocks``."""


def _user_id(primary_key: bool) -> sa.Column[str]:
    """A column naming the user a row belongs to, deleted with the user."""
    return sa.Column(
        "user_id",
        sa.String(length=36),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=primary_key,
        nullable=False,
    )


def upgrade() -> None:
    """Create the listings, companies, opt-in, per-user run, and per-user universe tables."""
    op.create_table(
        "screener_listings",
        sa.Column("symbol", sa.String(length=16), primary_key=True),
        sa.Column("cik", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("exchange", sa.String(length=16), nullable=False),
        sa.Column("shares_outstanding", sa.Float(), nullable=True),
    )
    op.create_index("ix_screener_listings_cik", "screener_listings", ["cik"])
    op.create_table(
        "screener_companies",
        sa.Column("cik", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("sic", sa.Integer(), nullable=True),
        sa.Column("sic_description", sa.String(length=160), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "nightly_refreshes",
        _user_id(primary_key=True),
        sa.Column("refresh_time", sa.String(length=5), nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.Column("credentials", sa.Text(), nullable=False),
        sa.Column("providers", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("keys_updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "user_screener_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        _user_id(primary_key=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stocks", sa.Integer(), nullable=True),
        sa.Column("priced", sa.Integer(), nullable=True),
        sa.Column("prices_as_of", sa.Date(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_user_screener_runs_user_started_at", "user_screener_runs", ["user_id", "started_at"]
    )
    op.create_table(
        "user_screener_stocks",
        _user_id(primary_key=True),
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
    op.create_index("ix_user_screener_stocks_cik", "user_screener_stocks", ["cik"])
    op.create_index("ix_user_screener_stocks_sector", "user_screener_stocks", ["sector"])
    op.create_index(
        "ix_user_screener_stocks_market_cap_rank", "user_screener_stocks", ["market_cap_rank"]
    )


def downgrade() -> None:
    """Drop the tables; the stored keys go with ``nightly_refreshes``."""
    op.drop_index("ix_user_screener_stocks_market_cap_rank", table_name="user_screener_stocks")
    op.drop_index("ix_user_screener_stocks_sector", table_name="user_screener_stocks")
    op.drop_index("ix_user_screener_stocks_cik", table_name="user_screener_stocks")
    op.drop_table("user_screener_stocks")
    op.drop_index("ix_user_screener_runs_user_started_at", table_name="user_screener_runs")
    op.drop_table("user_screener_runs")
    op.drop_table("nightly_refreshes")
    op.drop_table("screener_companies")
    op.drop_index("ix_screener_listings_cik", table_name="screener_listings")
    op.drop_table("screener_listings")
