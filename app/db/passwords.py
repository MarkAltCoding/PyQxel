"""Storage of passwords and of the links emailed to choose one.

An address is proven before anything is attached to it. Asking for a link stores only
the link; the account is made, or a password added to the account already using the
address, when the link is used. So someone who types another person's address can
neither reach that person's account nor make one in their name for them to join later.
"""

import asyncio
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.passwords import hash_password, needs_rehash, verify_password
from app.core.security import hash_api_key
from app.db.tables import ApiKeyRecord, EmailLinkRecord, UserRecord, as_utc
from app.db.users import User, create_user, find_user_by_email, get_user

EMAIL_LINK_MINUTES: int = 60
"""How long an emailed link works."""

PASSWORD_SIGN_IN = "email"
"""What ``api_keys.signed_in_with`` holds for keys issued by signing in with a password."""


def normalize_email(email: str) -> str:
    """An address as accounts store it: trimmed and in lower case."""
    return email.strip().lower()


@dataclass(frozen=True)
class EmailLink:
    """A new link's token, which goes only into the email, and what the address has."""

    token: str
    has_account: bool
    has_password: bool


async def new_email_link(session: AsyncSession, email: str) -> EmailLink:
    """Make a link for ``email`` to choose a password with, replacing any earlier one.

    Links that have expired, for any address, are deleted too.
    """
    email = normalize_email(email)
    now = datetime.now(timezone.utc)
    await session.execute(
        delete(EmailLinkRecord).where(
            (EmailLinkRecord.email == email) | (EmailLinkRecord.expires_at <= now)
        )
    )
    token = secrets.token_urlsafe(32)
    session.add(
        EmailLinkRecord(
            token_hash=hash_api_key(token),
            email=email,
            created_at=now,
            expires_at=now + timedelta(minutes=EMAIL_LINK_MINUTES),
        )
    )
    record = await session.scalar(select(UserRecord).where(UserRecord.email == email))
    await session.commit()
    return EmailLink(
        token=token,
        has_account=record is not None,
        has_password=record is not None and record.password_hash is not None,
    )


async def _live_link(session: AsyncSession, token: str) -> EmailLinkRecord | None:
    record = await session.get(EmailLinkRecord, hash_api_key(token))
    if record is None or as_utc(record.expires_at) <= datetime.now(timezone.utc):
        return None
    return record


async def email_for_link(session: AsyncSession, token: str) -> str | None:
    """The address a working link was sent to, or ``None``. Does not use it up."""
    record = await _live_link(session, token)
    return None if record is None else record.email


@dataclass(frozen=True)
class PasswordSet:
    """The account a link set a password for, and whether it replaced an older one."""

    user: User
    replaced: bool


async def use_email_link(session: AsyncSession, token: str, password: str) -> PasswordSet | None:
    """Set the password of the account at the link's address, making the account if
    there is none, and use the link up; ``None`` if the link is unknown or expired.

    Every link to the address goes with it. When an older password is replaced, as when
    someone forgot theirs or fears it is known, the keys signing in with it issued are
    revoked, so a device signed in by whoever knew it is signed out.
    """
    record = await _live_link(session, token)
    if record is None:
        return None
    email = record.email
    await session.execute(delete(EmailLinkRecord).where(EmailLinkRecord.email == email))
    await session.commit()
    user = await find_user_by_email(session, email) or await create_user(session, email)
    account = await session.get(UserRecord, user.id)
    assert account is not None
    replaced = account.password_hash is not None
    account.password_hash = await asyncio.to_thread(hash_password, password)
    if replaced:
        await session.execute(
            update(ApiKeyRecord)
            .where(
                ApiKeyRecord.user_id == user.id,
                ApiKeyRecord.signed_in_with == PASSWORD_SIGN_IN,
                ApiKeyRecord.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
    await session.commit()
    return PasswordSet(user=user, replaced=replaced)


async def user_for_password(session: AsyncSession, email: str, password: str) -> User | None:
    """The account ``email`` and ``password`` sign in to, or ``None`` if they do not match.

    Unknown addresses and accounts without a password take as long to refuse as a wrong
    password. A hash made with weaker parameters than today's is redone on the way.
    """
    record = await session.scalar(
        select(UserRecord).where(UserRecord.email == normalize_email(email))
    )
    stored = None if record is None else record.password_hash
    if not await asyncio.to_thread(verify_password, stored, password):
        return None
    assert record is not None and stored is not None
    if needs_rehash(stored):
        record.password_hash = await asyncio.to_thread(hash_password, password)
        await session.commit()
    return await get_user(session, record.id)
