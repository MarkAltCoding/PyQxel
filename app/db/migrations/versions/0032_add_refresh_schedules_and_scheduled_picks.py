"""Let the nightly refresh run on a schedule, and Claude's picks be made on one.

``nightly_refreshes`` gains ``schedule``: which days it runs on and each weekday's own
time, null for every night as before. ``scheduled_picks`` holds each user's weekly and
monthly picks schedules, the request they make, and the keys they run on, encrypted.

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the column, null for every refresh already stored, and the table."""
    with op.batch_alter_table("nightly_refreshes") as batch:
        batch.add_column(sa.Column("schedule", sa.JSON(), nullable=True))
    op.create_table(
        "scheduled_picks",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.Column("credentials", sa.Text(), nullable=False),
        sa.Column("providers", sa.JSON(), nullable=False),
        sa.Column("weekly", sa.JSON(), nullable=True),
        sa.Column("monthly", sa.JSON(), nullable=True),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("keys_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("weekly_ran_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("monthly_ran_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("running_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    """Drop the table and the column."""
    op.drop_table("scheduled_picks")
    with op.batch_alter_table("nightly_refreshes") as batch:
        batch.drop_column("schedule")
