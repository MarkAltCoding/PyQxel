"""Make API keys issued by signing in expire.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-21
"""

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRACE: timedelta = timedelta(days=30)
"""How long the sign-in keys issued before this migration keep working: the default
``SIGN_IN_KEY_DAYS``, counted from the upgrade so no one is signed out by it."""


def upgrade() -> None:
    """Add the expiry, give working sign-in keys one, and drop revoked sign-in keys."""
    op.add_column("api_keys", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    keys = sa.table(
        "api_keys",
        sa.column("signed_in_with", sa.String),
        sa.column("revoked_at", sa.DateTime(timezone=True)),
        sa.column("expires_at", sa.DateTime(timezone=True)),
    )
    signed_in = keys.c.signed_in_with.is_not(None)
    op.execute(
        keys.update()
        .where(signed_in, keys.c.revoked_at.is_(None))
        .values(expires_at=datetime.now(timezone.utc) + GRACE)
    )
    op.execute(keys.delete().where(signed_in, keys.c.revoked_at.is_not(None)))


def downgrade() -> None:
    """Drop the expiry; sign-in keys then last until revoked again."""
    with op.batch_alter_table("api_keys") as batch:
        batch.drop_column("expires_at")
