"""Storage of Claude's picks made on a schedule: who opted in, their encrypted keys, when
each schedule runs, and when it last did.

The picks are made while no browser is open, so turning a schedule on stores the user's
Anthropic key and market data keys, encrypted together (:func:`encrypt_secret`) and
decrypted only by the job that makes them. Turning both schedules off deletes them, as
deleting the account does (:func:`app.db.users.delete_user`).

A weekly schedule runs once a week on its day, a monthly one once a month on its day, both
at a local time. When both fall due in the same hourly check they run in the same job, one
after the other, so the second reuses what the first gathered while it is still cached.
A run never starts while the user's own screener refresh is running: it waits for the
check after the refresh finishes, so the picks come from the new universe.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.credentials import ProviderKeys
from app.core.security import decrypt_secret, encrypt_secret
from app.db.nightly import RUNNING, STALE_AFTER, _runs
from app.db.tables import ScheduledPicksRecord, as_utc
from app.models.picks import (
    Horizon,
    MonthlyPicksTime,
    PicksRequest,
    PicksScheduleSettings,
    PicksScheduleStatus,
    WeeklyPicksTime,
)
from app.models.nightly import NightlyRefreshSettings
from app.models.schedule import RefreshSchedule
from app.stats.schedule import (
    DEFAULT_REFRESH_MINUTES,
    CONFLICT_DAYS,
    moments,
    next_moment,
    overlaps,
)

Which = Literal["weekly", "monthly"]

STORED_KEYS: tuple[str, ...] = ("anthropic", "finnhub", "tiingo", "fmp", "eodhd", "alpha_vantage")
"""The keys a schedule may run on; Anthropic's is required."""

RUN_STALE_AFTER: timedelta = timedelta(hours=2)
"""How long a scheduled run may stay unfinished before it is taken to have stopped."""


class MissingAnthropicKeyError(ValueError):
    """Raised when scheduling picks without an Anthropic key, which writes them."""


class NothingScheduledError(ValueError):
    """Raised when neither a weekly nor a monthly schedule is given."""


def schedule_for(which: Which, record: ScheduledPicksRecord) -> tuple[RefreshSchedule, str] | None:
    """The day-picking schedule and time of ``record``'s ``which`` picks, or ``None`` if off."""
    if which == "weekly":
        if record.weekly is None:
            return None
        weekly = WeeklyPicksTime.model_validate(record.weekly)
        return RefreshSchedule(frequency="weekly", weekday=weekly.weekday), weekly.time
    if record.monthly is None:
        return None
    monthly = MonthlyPicksTime.model_validate(record.monthly)
    return RefreshSchedule(frequency="monthly", month_day=monthly.month_day), monthly.time


def next_picks_at(which: Which, record: ScheduledPicksRecord, now: datetime) -> datetime | None:
    """When ``record``'s ``which`` picks are next made, or ``None`` when that one is off."""
    found = schedule_for(which, record)
    if found is None:
        return None
    ran = getattr(record, f"{which}_ran_at")
    return next_moment(
        found[0], found[1], record.time_zone, now, None if ran is None else as_utc(ran)
    )


def horizon_for(which: Which) -> Horizon:
    """The holding period each schedule's picks are made for."""
    return Horizon(weeks=1) if which == "weekly" else Horizon(months=1)


async def enable(
    session: AsyncSession, user_id: str, settings: PicksScheduleSettings, keys: ProviderKeys
) -> None:
    """Turn ``user_id``'s schedules on or change them, storing ``keys`` encrypted.

    Raises:
        NothingScheduledError: If neither schedule is given.
        MissingAnthropicKeyError: If ``keys`` has no Anthropic key.
        CredentialsUnavailableError: If the server has no encryption key.
    """
    if settings.weekly is None and settings.monthly is None:
        raise NothingScheduledError("Choose weekly picks, monthly picks, or both.")
    plain = {
        name: secret.get_secret_value()
        for name in STORED_KEYS
        if (secret := getattr(keys, name)) is not None
    }
    if "anthropic" not in plain:
        raise MissingAnthropicKeyError(
            "Scheduled picks are written on your Anthropic key: add it in Settings first."
        )
    now = datetime.now(timezone.utc)
    record = await session.get(ScheduledPicksRecord, user_id)
    if record is None:
        record = ScheduledPicksRecord(user_id=user_id, created_at=now)
        session.add(record)
        # A schedule turned on runs first at its next time, not at once for a time gone by.
        record.weekly_ran_at = now
        record.monthly_ran_at = now
    record.time_zone = settings.time_zone
    record.credentials = encrypt_secret(json.dumps(plain))
    record.providers = list(plain)
    record.weekly = None if settings.weekly is None else settings.weekly.model_dump()
    record.monthly = None if settings.monthly is None else settings.monthly.model_dump()
    record.request = settings.request.model_dump(mode="json", exclude={"refresh"})
    record.keys_updated_at = now
    record.last_error = None
    await session.commit()


async def disable(session: AsyncSession, user_id: str) -> bool:
    """Turn both schedules off, deleting the stored keys; return whether any was on."""
    record = await session.get(ScheduledPicksRecord, user_id)
    if record is None:
        return False
    await session.delete(record)
    await session.commit()
    return True


async def schedule_status(
    session: AsyncSession, user_id: str, available: bool, now: datetime | None = None
) -> PicksScheduleStatus:
    """What is scheduled for ``user_id``, when it runs next, and how the last run went."""
    now = now or datetime.now(timezone.utc)
    record = await session.get(ScheduledPicksRecord, user_id)
    if record is None:
        return PicksScheduleStatus(available=available, enabled=False)
    return PicksScheduleStatus(
        available=available,
        enabled=True,
        weekly=None if record.weekly is None else WeeklyPicksTime.model_validate(record.weekly),
        monthly=None if record.monthly is None else MonthlyPicksTime.model_validate(record.monthly),
        time_zone=record.time_zone,
        request=PicksRequest.model_validate(record.request),
        providers=list(record.providers),
        next_weekly_at=next_picks_at("weekly", record, now),
        next_monthly_at=next_picks_at("monthly", record, now),
        weekly_ran_at=None if record.weekly_ran_at is None else as_utc(record.weekly_ran_at),
        monthly_ran_at=None if record.monthly_ran_at is None else as_utc(record.monthly_ran_at),
        last_error=record.last_error,
    )


@dataclass(frozen=True)
class DuePicks:
    """A user whose scheduled picks are due, which ones, and what they run on."""

    user_id: str
    which: list[Which]
    """``weekly``, ``monthly``, or both: those due in the same check run in one go."""
    keys: ProviderKeys
    request: PicksRequest
    """The request, without its holding period, which each schedule sets."""


def _keys(record: ScheduledPicksRecord) -> ProviderKeys:
    plain = json.loads(decrypt_secret(record.credentials).get_secret_value())
    if not isinstance(plain, dict):
        raise ValueError("The stored keys are not in the expected form.")

    def secret(name: str) -> SecretStr | None:
        value = plain.get(name)
        return SecretStr(value) if isinstance(value, str) and value else None

    return ProviderKeys(
        anthropic=secret("anthropic"),
        finnhub=secret("finnhub"),
        tiingo=secret("tiingo"),
        fmp=secret("fmp"),
        eodhd=secret("eodhd"),
        alpha_vantage=secret("alpha_vantage"),
    )


async def _screener_running(session: AsyncSession, user_id: str, now: datetime) -> bool:
    """Whether ``user_id``'s own screener refresh is running now."""
    runs = await _runs(session, user_id)
    return bool(
        runs and runs[0].status == RUNNING and now - as_utc(runs[0].started_at) < STALE_AFTER
    )


def _due(record: ScheduledPicksRecord, now: datetime) -> list[Which]:
    return [
        which
        for which in ("weekly", "monthly")
        if (moment := next_picks_at(which, record, now)) is not None and moment <= now
    ]


async def due_picks(
    session: AsyncSession, now: datetime, screeners_due: set[str] | None = None
) -> list[DuePicks]:
    """The users whose scheduled picks are due at ``now``, with their keys.

    A user whose own screener refresh is running is left for a later check, so their
    picks are made from the refreshed universe; one whose refresh is starting in the same
    job (``screeners_due``) is made after it by the job. A run still marked as running is
    skipped unless it is older than :data:`RUN_STALE_AFTER`. Keys that cannot be decrypted
    are recorded as the run's error.
    """
    found: list[DuePicks] = []
    for record in list(await session.scalars(select(ScheduledPicksRecord))):
        which = _due(record, now)
        if not which:
            continue
        if (
            record.running_since is not None
            and now - as_utc(record.running_since) < RUN_STALE_AFTER
        ):
            continue
        if record.user_id not in (screeners_due or set()) and await _screener_running(
            session, record.user_id, now
        ):
            continue
        try:
            keys = _keys(record)
        except ValueError as exc:
            record.last_error = (
                f"Your stored keys could not be read ({exc}). Turn scheduled picks off and on."
            )
            for name in which:
                setattr(record, f"{name}_ran_at", now)
            await session.commit()
            continue
        found.append(
            DuePicks(
                user_id=record.user_id,
                which=which,
                keys=keys,
                request=PicksRequest.model_validate(record.request),
            )
        )
    return found


async def any_picks_due(session: AsyncSession, now: datetime) -> bool:
    """Whether any user's scheduled picks are due at ``now``, changing nothing."""
    for record in list(await session.scalars(select(ScheduledPicksRecord))):
        if _due(record, now) and not await _screener_running(session, record.user_id, now):
            return True
    return False


async def start_picks_run(
    session: AsyncSession, user_id: str, which: list[Which], now: datetime
) -> None:
    """Mark ``which`` of ``user_id``'s schedules as run at ``now``, and the run as going."""
    record = await session.get(ScheduledPicksRecord, user_id)
    if record is None:
        return
    for name in which:
        setattr(record, f"{name}_ran_at", now)
    record.running_since = now
    await session.commit()


async def finish_picks_run(session: AsyncSession, user_id: str, error: str | None) -> None:
    """Record how ``user_id``'s scheduled run went."""
    record = await session.get(ScheduledPicksRecord, user_id)
    if record is None:
        return
    record.running_since = None
    record.last_error = error
    await session.commit()


MOST_WARNINGS: int = 4
"""Conflicts described at most; the rest follow the same pattern."""


async def schedule_conflicts(
    session: AsyncSession,
    user_id: str,
    nightly: NightlyRefreshSettings | None,
    picks: PicksScheduleSettings | None,
    now: datetime | None = None,
) -> list[str]:
    """Where ``user_id``'s scheduled picks would start during their screener refresh, over
    the next two months: ``nightly`` and ``picks`` as about to be saved, or as stored when
    left out. A refresh is taken to run as long as its last success did."""
    from zoneinfo import ZoneInfo

    from app.db.nightly import latest_success, schedule_of
    from app.db.tables import NightlyRefreshRecord

    now = now or datetime.now(timezone.utc)
    if nightly is None:
        record = await session.get(NightlyRefreshRecord, user_id)
        if record is None:
            return []
        nightly = NightlyRefreshSettings(
            refresh_time=record.refresh_time,
            time_zone=record.time_zone,
            schedule=schedule_of(record),
        )
    if picks is None:
        stored = await session.get(ScheduledPicksRecord, user_id)
        if stored is None:
            return []
        picks = PicksScheduleSettings(
            weekly=None if stored.weekly is None else WeeklyPicksTime.model_validate(stored.weekly),
            monthly=(
                None if stored.monthly is None else MonthlyPicksTime.model_validate(stored.monthly)
            ),
            time_zone=stored.time_zone,
        )
    success = await latest_success(session, user_id)
    minutes = DEFAULT_REFRESH_MINUTES
    if success is not None and success.finished_at is not None:
        taken = as_utc(success.finished_at) - as_utc(success.started_at)
        minutes = max(1, round(taken.total_seconds() / 60))
    starts = moments(
        nightly.schedule, nightly.refresh_time, nightly.time_zone, now, CONFLICT_DAYS + 1
    )
    zone = ZoneInfo(picks.time_zone)
    warnings: list[str] = []
    for which, timing in (("weekly", picks.weekly), ("monthly", picks.monthly)):
        if timing is None:
            continue
        schedule = (
            RefreshSchedule(frequency="weekly", weekday=timing.weekday)
            if isinstance(timing, WeeklyPicksTime)
            else RefreshSchedule(frequency="monthly", month_day=timing.month_day)
        )
        runs = moments(schedule, timing.time, picks.time_zone, now, CONFLICT_DAYS)
        for pick, start in overlaps(starts, minutes, runs):
            local, began = pick.astimezone(zone), start.astimezone(zone)
            warnings.append(
                f"Your {which} picks on {local:%A, %B} {local.day} at "
                f"{local:%-I:%M %p} fall during the screener refresh that starts at "
                f"{began:%-I:%M %p} and takes about {minutes} minutes; they would wait for "
                "it to finish, so they may come later."
            )
    return warnings[:MOST_WARNINGS]
