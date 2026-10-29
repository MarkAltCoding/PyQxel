"""Stop storing provider keys, and add settings for the whole server.

Browsers now keep provider keys and send them with each request, so the stored ones
are deleted. The admin's app-wide switches live in ``app_settings``.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY_COLUMNS: tuple[str, ...] = (
    "anthropic_api_key",
    "anthropic_key_hint",
    "fmp_api_key",
    "fmp_key_hint",
    "alpha_vantage_api_key",
    "alpha_vantage_key_hint",
    "finnhub_api_key",
    "finnhub_key_hint",
)


def upgrade() -> None:
    """Drop the stored keys and their hints; create ``app_settings``."""
    with op.batch_alter_table("users") as batch:
        for column in KEY_COLUMNS:
            batch.drop_column(column)
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("yfinance_fallback", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    """Restore empty key columns; the deleted keys cannot come back."""
    op.drop_table("app_settings")
    with op.batch_alter_table("users") as batch:
        for column in KEY_COLUMNS:
            kind = sa.String(length=16) if column.endswith("_hint") else sa.Text()
            batch.add_column(sa.Column(column, kind, nullable=True))
