"""Storage of accounts, their API keys and AI limits.

Provider keys are not stored: browsers keep them and send them with each request.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import API_KEY_DISPLAY_LENGTH, generate_api_key, hash_api_key
from app.db.tables import (
    AnalysisRecord,
    ApiKeyRecord,
    AssistantRequestRecord,
    BacktestRecord,
    HoldingRecord,
    IdentityRecord,
    PortfolioAnalysisRecord,
    PortfolioRecord,
    SimulationRecord,
    UserRecord,
    as_utc,
)
from app.models.account import AiLimits, AiUsage, ApiKeyInfo, NewApiKey

LAST_USED_RESOLUTION: timedelta = timedelta(minutes=1)
"""How stale a key's ``last_used_at`` may get, so busy keys are not written every request."""


class EmailTakenError(ValueError):
    """Raised when an account already uses the email address."""


@dataclass(frozen=True)
class User:
    """An authenticated account."""

    id: str
    email: str
    created_at: datetime
    limits: AiLimits
    yfinance_fallback: bool = False

    @property
    def is_admin(self) -> bool:
        """Whether this is the account ``ADMIN_EMAIL`` names; none is when it is unset."""
        admin = get_settings().admin_email
        return admin is not None and self.email == admin


def _user(record: UserRecord) -> User:
    return User(
        id=record.id,
        email=record.email,
        created_at=as_utc(record.created_at),
        limits=AiLimits(
            ai_requests_per_hour=record.ai_requests_per_hour,
            ai_requests_per_day=record.ai_requests_per_day,
        ),
        yfinance_fallback=bool(record.yfinance_fallback),
    )


def _key_info(record: ApiKeyRecord) -> ApiKeyInfo:
    return ApiKeyInfo(
        id=UUID(record.id),
        name=record.name,
        prefix=record.prefix,
        created_at=as_utc(record.created_at),
        last_used_at=None if record.last_used_at is None else as_utc(record.last_used_at),
        revoked_at=None if record.revoked_at is None else as_utc(record.revoked_at),
        expires_at=None if record.expires_at is None else as_utc(record.expires_at),
    )


def _expired(record: ApiKeyRecord, now: datetime) -> bool:
    """Whether ``record`` has passed its expiry, if it has one."""
    return record.expires_at is not None and as_utc(record.expires_at) <= now


async def create_user(session: AsyncSession, email: str) -> User:
    """Create an account for ``email``.

    Raises:
        EmailTakenError: If an account already uses ``email``.
    """
    email = email.strip().lower()
    if await session.scalar(select(UserRecord.id).where(UserRecord.email == email)):
        raise EmailTakenError(f"An account already uses {email}.")
    record = UserRecord(id=str(uuid4()), email=email, created_at=datetime.now(timezone.utc))
    session.add(record)
    await session.commit()
    return _user(record)


async def find_user_by_email(session: AsyncSession, email: str) -> User | None:
    """The account using ``email``, if any."""
    record = await session.scalar(
        select(UserRecord).where(UserRecord.email == email.strip().lower())
    )
    return None if record is None else _user(record)


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    """The account with ``user_id``, if any."""
    record = await session.get(UserRecord, user_id)
    return None if record is None else _user(record)


async def create_api_key(
    session: AsyncSession,
    user_id: str,
    name: str,
    signed_in_with: str | None = None,
    expires_after: timedelta | None = None,
) -> NewApiKey:
    """Issue an API key for the account; the key is returned this once and stored hashed.

    ``signed_in_with`` names the provider of a sign-in the key is issued for, which marks
    it as one that signing out revokes; the account's earlier sign-in keys that have
    been revoked or have expired are deleted then, so signing in often does not pile
    them up. ``expires_after`` limits how long the key works.
    """
    now = datetime.now(timezone.utc)
    if signed_in_with is not None:
        await session.execute(
            delete(ApiKeyRecord).where(
                ApiKeyRecord.user_id == user_id,
                ApiKeyRecord.signed_in_with.is_not(None),
                or_(ApiKeyRecord.revoked_at.is_not(None), ApiKeyRecord.expires_at <= now),
            )
        )
    key = generate_api_key()
    record = ApiKeyRecord(
        id=str(uuid4()),
        user_id=user_id,
        name=name,
        prefix=key[:API_KEY_DISPLAY_LENGTH],
        key_hash=hash_api_key(key),
        created_at=now,
        signed_in_with=signed_in_with,
        expires_at=None if expires_after is None else now + expires_after,
    )
    session.add(record)
    await session.commit()
    return NewApiKey(**_key_info(record).model_dump(), key=key)


async def user_for_identity(session: AsyncSession, provider: str, subject: str, email: str) -> User:
    """The account a GitHub or Google identity signs in to, made on its first sign-in.

    A known identity keeps its account, and its email is brought up to date. A new one
    joins the account that already uses its email, which the provider has verified, so
    the same person can sign in either way; failing that, a new account is made.
    """
    email = email.strip().lower()
    identity = await session.scalar(
        select(IdentityRecord).where(
            IdentityRecord.provider == provider, IdentityRecord.subject == subject
        )
    )
    if identity is not None:
        identity.email = email
        await session.commit()
        user = await get_user(session, identity.user_id)
        assert user is not None, "an identity's account is deleted with it"
        return user
    user = await find_user_by_email(session, email) or await create_user(session, email)
    session.add(
        IdentityRecord(
            id=str(uuid4()),
            user_id=user.id,
            provider=provider,
            subject=subject,
            email=email,
            created_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    return user


async def revoke_signed_in_key(session: AsyncSession, key: str) -> bool:
    """Revoke ``key`` if a sign-in issued it, as signing out does; keys made for scripts
    are left alone. Returns whether a key was revoked."""
    revoked = await session.execute(
        update(ApiKeyRecord)
        .where(
            ApiKeyRecord.key_hash == hash_api_key(key),
            ApiKeyRecord.signed_in_with.is_not(None),
            ApiKeyRecord.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await session.commit()
    return bool(revoked.rowcount)  # type: ignore[attr-defined]


async def authenticate(session: AsyncSession, key: str) -> User | None:
    """The account an API key belongs to, or ``None`` for unknown, revoked and expired keys."""
    record = await session.scalar(
        select(ApiKeyRecord).where(
            ApiKeyRecord.key_hash == hash_api_key(key), ApiKeyRecord.revoked_at.is_(None)
        )
    )
    now = datetime.now(timezone.utc)
    if record is None or _expired(record, now):
        return None
    if record.last_used_at is None or now - as_utc(record.last_used_at) > LAST_USED_RESOLUTION:
        record.last_used_at = now
        await session.commit()
    return await get_user(session, record.user_id)


async def list_api_keys(session: AsyncSession, user_id: str) -> list[ApiKeyInfo]:
    """The account's API keys, oldest first, revoked ones included."""
    records = await session.scalars(
        select(ApiKeyRecord)
        .where(ApiKeyRecord.user_id == user_id)
        .order_by(ApiKeyRecord.created_at, ApiKeyRecord.id)
    )
    return [_key_info(record) for record in records]


async def revoke_api_key(session: AsyncSession, user_id: str, key_id: UUID) -> bool:
    """Revoke one of the account's keys; return whether it had such a key."""
    record = await session.get(ApiKeyRecord, str(key_id))
    if record is None or record.user_id != user_id:
        return False
    if record.revoked_at is None:
        record.revoked_at = datetime.now(timezone.utc)
        await session.commit()
    return True


async def update_limits(session: AsyncSession, user_id: str, limits: AiLimits) -> User:
    """Replace the account's AI request limits."""
    record = await session.get(UserRecord, user_id)
    assert record is not None
    record.ai_requests_per_hour = limits.ai_requests_per_hour
    record.ai_requests_per_day = limits.ai_requests_per_day
    await session.commit()
    return _user(record)


async def update_yfinance_fallback(session: AsyncSession, user_id: str, enabled: bool) -> User:
    """Turn the account's yfinance fallback on or off; it applies only to the admin."""
    record = await session.get(UserRecord, user_id)
    assert record is not None
    record.yfinance_fallback = enabled
    await session.commit()
    return _user(record)


async def ai_usage(session: AsyncSession, user_id: str) -> AiUsage:
    """New AI requests made for the account in the last hour and day: stock analyses,
    portfolio reviews and coding assistant answers."""
    now = datetime.now(timezone.utc)

    async def since(window: timedelta) -> int:
        total = 0
        for table in (AnalysisRecord, PortfolioAnalysisRecord, AssistantRequestRecord):
            count = await session.scalar(
                select(func.count())
                .select_from(table)
                .where(table.user_id == user_id, table.created_at >= now - window)
            )
            total += count or 0
        return total

    return AiUsage(
        last_hour=await since(timedelta(hours=1)), last_day=await since(timedelta(days=1))
    )


async def claim_unowned(session: AsyncSession, user_id: str) -> dict[str, int]:
    """Give the account every stored result that has no owner; return counts per table."""
    claimed: dict[str, int] = {}
    for table in (BacktestRecord, AnalysisRecord, SimulationRecord):
        result = await session.execute(
            update(table).where(table.user_id.is_(None)).values(user_id=user_id)
        )
        claimed[table.__tablename__] = int(result.rowcount)  # type: ignore[attr-defined]
    await session.commit()
    return claimed


async def delete_user(session: AsyncSession, user_id: str) -> bool:
    """Delete the account and everything it holds; return whether it existed.

    Its API keys, sign-in identities, portfolios and their holdings, stored backtests,
    analyses, portfolio reviews and simulations, and its count of assistant answers go
    with it. Rows are deleted explicitly rather than left to the foreign keys' cascades,
    which SQLite does not enforce by default.
    """
    record = await session.get(UserRecord, user_id)
    if record is None:
        return False
    for table in (
        ApiKeyRecord,
        IdentityRecord,
        HoldingRecord,
        PortfolioRecord,
        BacktestRecord,
        AnalysisRecord,
        PortfolioAnalysisRecord,
        AssistantRequestRecord,
        SimulationRecord,
    ):
        await session.execute(delete(table).where(table.user_id == user_id))
    await session.delete(record)
    await session.commit()
    return True
