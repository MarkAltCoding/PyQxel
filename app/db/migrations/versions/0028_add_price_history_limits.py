"""Give each price-history key its own nightly limit, and record each run's choice.

``nightly_refreshes.price_history_limits`` replaces ``price_history_stocks``, one cap for
every key, with one per key; refreshes already on take each key's free plan's allowance.
``user_screener_runs.choice`` records what a run rebuilt, so its request counts estimate
only a refresh of the same choice.

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace the single cap with one per key, and add each run's choice."""
    with op.batch_alter_table("nightly_refreshes") as batch:
        batch.add_column(sa.Column("price_history_limits", sa.JSON(), nullable=True))
        batch.drop_column("price_history_stocks")
    with op.batch_alter_table("user_screener_runs") as batch:
        batch.add_column(sa.Column("choice", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Go back to one cap for every key, which starts unset."""
    with op.batch_alter_table("user_screener_runs") as batch:
        batch.drop_column("choice")
    with op.batch_alter_table("nightly_refreshes") as batch:
        batch.add_column(sa.Column("price_history_stocks", sa.Integer(), nullable=True))
        batch.drop_column("price_history_limits")
