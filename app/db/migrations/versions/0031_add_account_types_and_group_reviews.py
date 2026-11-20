"""Say what kind of account each portfolio is, and which portfolios a review covers.

``portfolios`` gains ``account_type`` (such as a Roth IRA) and ``contribution_room`` (how
much more may be put into it this year). ``portfolio_analyses`` gains ``group_mode`` and
``group_members`` for a review of several portfolios at once: reviewed as one
(``combined``) or each with its own plan (``separate``), and which ones, as ``,1,3,``.

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the columns, empty for what is already stored."""
    with op.batch_alter_table("portfolios") as batch:
        batch.add_column(sa.Column("account_type", sa.String(length=24), nullable=True))
        batch.add_column(sa.Column("contribution_room", sa.Float(), nullable=True))
    with op.batch_alter_table("portfolio_analyses") as batch:
        batch.add_column(sa.Column("group_mode", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("group_members", sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Drop the columns."""
    with op.batch_alter_table("portfolio_analyses") as batch:
        batch.drop_column("group_members")
        batch.drop_column("group_mode")
    with op.batch_alter_table("portfolios") as batch:
        batch.drop_column("contribution_room")
        batch.drop_column("account_type")
