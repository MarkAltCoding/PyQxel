"""Storage of nightly screener refreshes: who opted in, their encrypted keys, their runs,
and the universe each one built.

Opting in is the only way PyQxel stores provider keys. They are encrypted together as one
JSON object (:func:`app.core.security.encrypt_secret`) and decrypted only by the refresh
job, never to answer a request. Opting out deletes them at once, as deleting the account
does (:func:`app.db.users.delete_user`).
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.credentials import ProviderKeys
from app.core.security import CredentialsUnavailableError, decrypt_secret, encrypt_secret
from app.db.tables import (
    NightlyRefreshRecord,
    UserScreenerRunRecord,
    UserScreenerStockRecord,
    as_utc,
)
from app.models.nightly import (
    STORED_PROVIDERS,
    NightlyProvider,
    NightlyRefreshSettings,
    NightlyRefreshStatus,
    NightlyRun,
    RunStatus,
)
from app.models.screener import ScreenedStock

RUNNING, SUCCEEDED, FAILED = "running", "succeeded", "failed"
"""States of a user's refresh run."""

STALE_AFTER: timedelta = timedelta(hours=8)
"""How long a run may stay running before it is taken to have stopped without finishing,
as when its job was cut off; the user's next refresh may then start."""

REQUIRED_PROVIDER: NightlyProvider = "finnhub"
"""The key a refresh cannot run without: Finnhub's free metrics fill the screener."""


class MissingFinnhubKeyError(ValueError):
    """Raised when opting in without a Finnhub key, which the refresh needs."""


@dataclass(frozen=True)
class DueRefresh:
    """A user whose refresh is due, and the keys it runs on."""

    user_id: str
    keys: ProviderKeys


def stored_providers(keys: ProviderKeys) -> list[NightlyProvider]:
    """The providers among :data:`STORED_PROVIDERS` whose key ``keys`` holds."""
    return [provider for provider in STORED_PROVIDERS if getattr(keys, provider) is not None]


async def enable(
    session: AsyncSession, user_id: str, settings: NightlyRefreshSettings, keys: ProviderKeys
) -> None:
    """Opt ``user_id`` in, or change their time, storing ``keys`` encrypted.

    Only market data keys are stored; an Anthropic key, if sent, is not. Keys stored
    earlier are replaced, so a key removed in the browser is removed here too.

    Raises:
        MissingFinnhubKeyError: If ``keys`` has no Finnhub key.
        CredentialsUnavailableError: If the server has no encryption key.
    """
    providers = stored_providers(keys)
    if REQUIRED_PROVIDER not in providers:
        raise MissingFinnhubKeyError(
            "The nightly refresh needs your Finnhub key: add it in Settings first."
        )
    plain = {
        provider: secret.get_secret_value()
        for provider in providers
        if (secret := getattr(keys, provider)) is not None
    }
    credentials = encrypt_secret(json.dumps(plain))
    now = datetime.now(timezone.utc)
    record = await session.get(NightlyRefreshRecord, user_id)
    if record is None:
        record = NightlyRefreshRecord(user_id=user_id, created_at=now)
        session.add(record)
    record.refresh_time = settings.refresh_time
    record.time_zone = settings.time_zone
    record.credentials = credentials
    record.providers = list(providers)
    record.keys_updated_at = now
    await session.commit()


async def disable(session: AsyncSession, user_id: str) -> bool:
    """Opt ``user_id`` out, deleting their stored keys; return whether they had opted in.

    Their last universe stays, shown with its date, until they delete their account.
    """
    result = await session.execute(
        delete(NightlyRefreshRecord).where(NightlyRefreshRecord.user_id == user_id)
    )
    await session.commit()
    return bool(getattr(result, "rowcount", 0))


def _keys(record: NightlyRefreshRecord) -> ProviderKeys:
    """The keys stored with ``record``, decrypted, for the refresh to run on.

    Raises:
        CredentialsUnavailableError: If they cannot be decrypted.
    """
    plain = json.loads(decrypt_secret(record.credentials).get_secret_value())
    if not isinstance(plain, dict):
        raise ValueError("The stored keys are not in the expected form.")
    return ProviderKeys(
        finnhub=_secret(plain.get("finnhub")),
        tiingo=_secret(plain.get("tiingo")),
        fmp=_secret(plain.get("fmp")),
        eodhd=_secret(plain.get("eodhd")),
        alpha_vantage=_secret(plain.get("alpha_vantage")),
    )


def _secret(value: object) -> SecretStr | None:
    """A stored key, or ``None`` when none was stored."""
    return SecretStr(value) if isinstance(value, str) and value else None


def scheduled_moment(refresh_time: str, time_zone: str, day: date) -> datetime:
    """``refresh_time`` on ``day`` in ``time_zone``, as aware UTC."""
    hour, minute = (int(part) for part in refresh_time.split(":"))
    local = datetime.combine(day, time(hour, minute), tzinfo=ZoneInfo(time_zone))
    return local.astimezone(timezone.utc)


def next_run_at(
    refresh_time: str, time_zone: str, now: datetime, last_started: datetime | None
) -> datetime:
    """When a refresh is next due: today's moment, unless a run has started since it.

    A moment already past today is due at once, so a user who opts in in the evening
    gets their first universe that night.
    """
    today = now.astimezone(ZoneInfo(time_zone)).date()
    moment = scheduled_moment(refresh_time, time_zone, today)
    if last_started is not None and last_started >= moment:
        return scheduled_moment(refresh_time, time_zone, today + timedelta(days=1))
    return moment


async def _runs(
    session: AsyncSession, user_id: str, status: str | None = None, limit: int = 1
) -> list[UserScreenerRunRecord]:
    """``user_id``'s latest runs, newest first, optionally only those in ``status``."""
    query = select(UserScreenerRunRecord).where(UserScreenerRunRecord.user_id == user_id)
    if status is not None:
        query = query.where(UserScreenerRunRecord.status == status)
    records = await session.scalars(
        query.order_by(UserScreenerRunRecord.started_at.desc()).limit(limit)
    )
    return list(records)


async def latest_success(session: AsyncSession, user_id: str) -> UserScreenerRunRecord | None:
    """``user_id``'s latest refresh that succeeded, whose universe is stored."""
    runs = await _runs(session, user_id, SUCCEEDED)
    return runs[0] if runs else None


def _run(record: UserScreenerRunRecord) -> NightlyRun:
    status: RunStatus = (
        "running"
        if record.status == RUNNING
        else "failed" if record.status == FAILED else "succeeded"
    )
    return NightlyRun(
        status=status,
        started_at=as_utc(record.started_at),
        finished_at=None if record.finished_at is None else as_utc(record.finished_at),
        stocks=record.stocks,
        priced=record.priced,
        prices_as_of=record.prices_as_of,
        error=record.error,
    )


async def refresh_status(
    session: AsyncSession, user_id: str, available: bool, now: datetime | None = None
) -> NightlyRefreshStatus:
    """Whether ``user_id`` opted in, what is stored, and how their refreshes went."""
    record = await session.get(NightlyRefreshRecord, user_id)
    latest = await _runs(session, user_id)
    success = await latest_success(session, user_id)
    status = NightlyRefreshStatus(
        available=available,
        enabled=record is not None,
        latest_run=_run(latest[0]) if latest else None,
        last_success=None if success is None else _run(success),
    )
    if record is None:
        return status
    started = as_utc(latest[0].started_at) if latest else None
    return status.model_copy(
        update={
            "refresh_time": record.refresh_time,
            "time_zone": record.time_zone,
            "providers": [p for p in record.providers if p in STORED_PROVIDERS],
            "keys_updated_at": as_utc(record.keys_updated_at),
            "next_run_at": next_run_at(
                record.refresh_time,
                record.time_zone,
                now or datetime.now(timezone.utc),
                started,
            ),
        }
    )


async def due_refreshes(session: AsyncSession, now: datetime) -> list[DueRefresh]:
    """The users whose refresh is due at ``now``, with their decrypted keys.

    A user whose last run is still running is skipped, unless it started more than
    :data:`STALE_AFTER` ago, when it is recorded as failed and the user is due again.
    A user whose keys cannot be decrypted gets a failed run saying so.
    """
    due: list[DueRefresh] = []
    for record in list(await session.scalars(select(NightlyRefreshRecord))):
        runs = await _runs(session, record.user_id)
        last = runs[0] if runs else None
        if last is not None and last.status == RUNNING:
            if now - as_utc(last.started_at) < STALE_AFTER:
                continue
            last.status = FAILED
            last.finished_at = now
            last.error = "The refresh stopped without finishing."
            await session.commit()
        started = None if last is None else as_utc(last.started_at)
        if next_run_at(record.refresh_time, record.time_zone, now, started) > now:
            continue
        try:
            keys = _keys(record)
        except (CredentialsUnavailableError, ValueError) as exc:
            # Recorded for the user to see, rather than stopping everyone else's refresh.
            run_id = await start_run(session, record.user_id, now)
            await finish_run(
                session,
                run_id,
                error=f"Your stored keys could not be read ({exc}) Turn the nightly refresh "
                "off and on again in Settings.",
            )
            continue
        due.append(DueRefresh(user_id=record.user_id, keys=keys))
    return due


async def start_run(session: AsyncSession, user_id: str, now: datetime | None = None) -> str:
    """Record ``user_id``'s refresh as started; return its ID."""
    run_id = str(uuid4())
    session.add(
        UserScreenerRunRecord(
            id=run_id,
            user_id=user_id,
            started_at=now or datetime.now(timezone.utc),
            status=RUNNING,
        )
    )
    await session.commit()
    return run_id


async def finish_run(
    session: AsyncSession,
    run_id: str,
    *,
    error: str | None = None,
    stocks: int | None = None,
    priced: int | None = None,
    prices_as_of: date | None = None,
) -> None:
    """Record a user's refresh as failed with ``error``, or succeeded with its results."""
    run = await session.get(UserScreenerRunRecord, run_id)
    if run is None:
        return
    run.finished_at = datetime.now(timezone.utc)
    run.status = FAILED if error else SUCCEEDED
    run.error = error
    run.stocks = stocks
    run.priced = priced
    run.prices_as_of = prices_as_of
    await session.commit()


async def replace_user_universe(
    session: AsyncSession, user_id: str, stocks: Iterable[ScreenedStock]
) -> int:
    """Replace ``user_id``'s own universe with ``stocks`` in one transaction; return the count."""
    rows = [UserScreenerStockRecord(user_id=user_id, **stock.model_dump()) for stock in stocks]
    await session.execute(
        delete(UserScreenerStockRecord).where(UserScreenerStockRecord.user_id == user_id)
    )
    session.add_all(rows)
    await session.commit()
    return len(rows)
