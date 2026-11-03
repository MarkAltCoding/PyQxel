"""Keep unlicensed market data to the account allowed to see it.

The server-wide switch in ``app_settings`` is gone, so its table is dropped. Each screener
refresh records whether it used licensed data alone; the existing ones did not, so they
are marked unlicensed.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop ``app_settings``; add ``screener_runs.licensed``, false for past refreshes."""
    op.drop_table("app_settings")
    with op.batch_alter_table("screener_runs") as batch:
        batch.add_column(
            sa.Column("licensed", sa.Boolean(), server_default=sa.false(), nullable=False)
        )


def downgrade() -> None:
    """Restore an empty ``app_settings``, with the switch off; drop the licensed marks."""
    with op.batch_alter_table("screener_runs") as batch:
        batch.drop_column("licensed")
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("yfinance_fallback", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
