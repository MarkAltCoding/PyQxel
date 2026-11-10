"""Bill users for their nightly refreshes, at cost, through Stripe.

``billing_accounts`` holds each paying user's Stripe customer, prepaid credit, and usage
subscription; ``billing_entries`` every top-up, refund, dispute, and refresh charged; and
``user_screener_runs.cost_micros`` what each refresh cost to compute.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the billing tables and each refresh's cost."""
    op.create_table(
        "billing_accounts",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("stripe_customer_id", sa.String(255), nullable=False, unique=True),
        sa.Column("credit_micros", sa.BigInteger(), nullable=False),
        sa.Column("subscription_id", sa.String(255), nullable=True),
        sa.Column("subscription_status", sa.String(32), nullable=True),
        sa.Column("subscription_interval", sa.String(8), nullable=True),
        sa.Column("subscription_renews_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subscription_cancels", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )  # fmt: skip
    op.create_table(
        "billing_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("credit_micros", sa.BigInteger(), nullable=False),
        sa.Column("subscription_micros", sa.BigInteger(), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stripe_ref", sa.String(255), nullable=True, unique=True),
        sa.Column("payment_intent", sa.String(255), nullable=True),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )  # fmt: skip
    op.create_index(
        "ix_billing_entries_user_created_at", "billing_entries", ["user_id", "created_at"]
    )
    op.create_index("ix_billing_entries_payment_intent", "billing_entries", ["payment_intent"])
    with op.batch_alter_table("user_screener_runs") as batch:
        batch.add_column(sa.Column("cost_micros", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    """Drop the billing tables and each refresh's cost."""
    with op.batch_alter_table("user_screener_runs") as batch:
        batch.drop_column("cost_micros")
    op.drop_index("ix_billing_entries_payment_intent", table_name="billing_entries")
    op.drop_index("ix_billing_entries_user_created_at", table_name="billing_entries")
    op.drop_table("billing_entries")
    op.drop_table("billing_accounts")
