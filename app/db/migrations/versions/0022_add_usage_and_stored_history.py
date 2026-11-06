"""Count provider requests, keep each user's price history, and let the nightly refresh
be limited.

``provider_usage`` counts the requests PyQxel sends on each provider key per hour, and
``provider_usage_symbols`` the different symbols a month, both under a one-way hash of the
key. ``stored_price_history`` keeps each user's daily bars, fetched on their own keys, for
them alone. ``nightly_refreshes.price_history_stocks`` caps the price histories a nightly
refresh fetches, and ``user_screener_runs.usage`` records the requests a run sent.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the usage and stored-history tables, and add the nightly refresh's columns."""
    op.create_table(
        "provider_usage",
        sa.Column("key_id", sa.String(length=16), primary_key=True),
        sa.Column("provider", sa.String(length=16), primary_key=True),
        sa.Column("hour", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "provider_usage_symbols",
        sa.Column("key_id", sa.String(length=16), primary_key=True),
        sa.Column("provider", sa.String(length=16), primary_key=True),
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("symbol", sa.String(length=32), primary_key=True),
    )
    op.create_table(
        "stored_price_history",
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(length=16), primary_key=True),
        sa.Column("symbol", sa.String(length=32), primary_key=True),
        sa.Column("requested_from", sa.Date(), nullable=False),
        sa.Column("first_day", sa.Date(), nullable=False),
        sa.Column("last_day", sa.Date(), nullable=False),
        sa.Column("bars", sa.Integer(), nullable=False),
        sa.Column("notice", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
    )
    with op.batch_alter_table("nightly_refreshes") as batch:
        batch.add_column(sa.Column("price_history_stocks", sa.Integer(), nullable=True))
    with op.batch_alter_table("user_screener_runs") as batch:
        batch.add_column(sa.Column("usage", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Drop the tables and columns; the counts and stored prices are lost."""
    with op.batch_alter_table("user_screener_runs") as batch:
        batch.drop_column("usage")
    with op.batch_alter_table("nightly_refreshes") as batch:
        batch.drop_column("price_history_stocks")
    op.drop_table("stored_price_history")
    op.drop_table("provider_usage_symbols")
    op.drop_table("provider_usage")
