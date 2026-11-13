"""Record the country a turned-away payment came from.

PyQxel does not take payments from the European Union or the United Kingdom, whose VAT it
is not registered for. ``billing_accounts.closed_country`` keeps the country of an
account whose payment was turned away, so it is not offered another.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the country, unset for every account."""
    with op.batch_alter_table("billing_accounts") as batch:
        batch.add_column(sa.Column("closed_country", sa.String(length=2), nullable=True))


def downgrade() -> None:
    """Drop the country."""
    with op.batch_alter_table("billing_accounts") as batch:
        batch.drop_column("closed_country")
