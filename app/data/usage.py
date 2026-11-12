"""How much of each provider plan a user's keys have used, and what each plan allows.

Every request PyQxel sends on a user's key is counted here (:func:`record`), by provider
and by a one-way hash of the key (:func:`key_id`), never the key itself. Counts collect in
memory and are written to the database every few seconds (:func:`flush`), one row per key
and clock hour, so every server instance and the nightly job add to the same totals.

Where a provider reports usage itself, that is what Settings shows, since it also counts
requests sent from outside PyQxel:

- **EODHD** answers ``/api/user`` with the day's calls and the plan's daily allowance, and
  sends ``X-RateLimit-Limit`` and ``X-RateLimit-Remaining`` for the current minute.
- **Finnhub** sends ``X-Ratelimit-Limit``, ``X-Ratelimit-Remaining``, and
  ``X-Ratelimit-Reset`` (epoch seconds) for the current minute.
- **Anthropic** sends ``anthropic-ratelimit-*`` headers for requests and tokens a minute,
  which refill continuously.

Tiingo, Financial Modeling Prep, and Alpha Vantage report nothing, so for them PyQxel's own
count is shown, labeled as covering only PyQxel's requests. The plans' limits are those
published on the providers' pages, as ``docs/data-sources.md`` records them.
"""

import asyncio
import hashlib
import logging
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db.tables import ProviderSymbolRecord, ProviderUsageRecord
from app.models.usage import PlanChoice, ProviderUsage, UsageLimit

logger = logging.getLogger(__name__)

UsageProvider = Literal["tiingo", "fmp", "eodhd", "finnhub", "alpha_vantage", "anthropic"]
"""Providers whose keys a user enters in Settings."""

Window = Literal["second", "minute", "hour", "day", "month"]
Unit = Literal["requests", "calls", "symbols", "bytes", "tokens", "input_tokens", "output_tokens"]

FLUSH_SECONDS: float = 5.0
"""How often counts kept in memory are written to the database."""
KEEP_DAYS: int = 40
"""How long hourly counts and monthly symbol lists are kept: a month and some."""
REPORTED_FRESH_SECONDS: float = 15 * 60
"""How long figures a provider reported in its headers are shown."""

EASTERN_STANDARD = timezone(timedelta(hours=-5), "EST")
"""US Eastern Standard Time all year round, which Tiingo's daily and monthly limits reset
by ("midnight EST")."""

PROVIDER_NAMES: dict[UsageProvider, str] = {
    "tiingo": "Tiingo",
    "fmp": "Financial Modeling Prep",
    "eodhd": "EODHD",
    "finnhub": "Finnhub",
    "alpha_vantage": "Alpha Vantage",
    "anthropic": "Anthropic",
}


@dataclass(frozen=True)
class PlanLimit:
    """One limit of a plan: at most ``limit`` of ``unit`` in each ``window``."""

    window: Window
    limit: int
    unit: Unit = "requests"


@dataclass(frozen=True)
class Plan:
    """A provider plan and its published limits."""

    id: str
    name: str
    limits: tuple[PlanLimit, ...]
    verified: bool = True
    """False when the limits come from third-party listings, not the provider's page."""


GB: int = 1024**3

PLANS: dict[UsageProvider, tuple[Plan, ...]] = {
    "tiingo": (
        Plan(
            "free",
            "Starter (free)",
            (
                PlanLimit("hour", 50),
                PlanLimit("day", 1_000),
                PlanLimit("month", 500, "symbols"),
                PlanLimit("month", 1 * GB, "bytes"),
            ),
        ),
        Plan(
            "power",
            "Power",
            (
                PlanLimit("hour", 10_000),
                PlanLimit("day", 100_000),
                PlanLimit("month", 40 * GB, "bytes"),
            ),
        ),
    ),
    "fmp": (
        Plan("free", "Basic (free)", (PlanLimit("day", 250),)),
        Plan("starter", "Starter", (PlanLimit("minute", 300),), verified=False),
        Plan("premium", "Premium", (PlanLimit("minute", 750),), verified=False),
        Plan("ultimate", "Ultimate", (PlanLimit("minute", 3_000),), verified=False),
    ),
    "eodhd": (
        Plan(
            "free",
            "Free",
            (PlanLimit("minute", 1_000), PlanLimit("day", 20, "calls")),
        ),
        Plan(
            "paid",
            "A paid plan",
            (PlanLimit("minute", 1_000), PlanLimit("day", 100_000, "calls")),
        ),
    ),
    "finnhub": (
        Plan("free", "Free", (PlanLimit("second", 30), PlanLimit("minute", 60))),
        Plan(
            "basic",
            "Basic",
            (PlanLimit("second", 30), PlanLimit("minute", 150)),
            verified=False,
        ),
        Plan(
            "standard",
            "Standard",
            (PlanLimit("second", 30), PlanLimit("minute", 300)),
            verified=False,
        ),
        Plan(
            "professional",
            "Professional",
            (PlanLimit("second", 30), PlanLimit("minute", 900)),
            verified=False,
        ),
    ),
    "alpha_vantage": (
        Plan("free", "Free", (PlanLimit("day", 25),)),
        Plan("premium_75", "Premium, 75 a minute", (PlanLimit("minute", 75),)),
        Plan("premium_150", "Premium, 150 a minute", (PlanLimit("minute", 150),)),
        Plan("premium_300", "Premium, 300 a minute", (PlanLimit("minute", 300),)),
        Plan("premium_600", "Premium, 600 a minute", (PlanLimit("minute", 600),)),
        Plan("premium_1200", "Premium, 1,200 a minute", (PlanLimit("minute", 1_200),)),
    ),
    "anthropic": (),
}
"""Each provider's plans, the free one first, with the limits its pricing page publishes.
Anthropic's limits depend on the account's usage tier and are read from its headers."""

DAY_ZONES: dict[UsageProvider, timezone] = {
    "tiingo": EASTERN_STANDARD,
    "eodhd": UTC,
    "fmp": UTC,
    "alpha_vantage": UTC,
    "finnhub": UTC,
}
"""The time zone whose midnight starts each provider's day and month."""

RESET_KNOWN: dict[UsageProvider, bool] = {
    "tiingo": True,
    "eodhd": True,
    "fmp": False,
    "alpha_vantage": False,
    "finnhub": True,
}
"""Whether the provider says when its daily limit resets. FMP and Alpha Vantage do not, so
their days are counted from midnight UTC, and Settings says so."""

SYMBOL_LIMITED: frozenset[UsageProvider] = frozenset({"tiingo"})
"""Providers whose plans limit the different symbols asked for in a month."""


def key_id(api_key: str) -> str:
    """A short one-way hash naming a key, so a key never reaches memory or the database."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def plan_for(provider: UsageProvider, plan_id: str | None) -> Plan | None:
    """The plan named ``plan_id``, or the provider's free plan."""
    plans = PLANS.get(provider, ())
    return next((plan for plan in plans if plan.id == plan_id), plans[0] if plans else None)


# Window arithmetic.


def window_start(provider: UsageProvider, window: Window, now: datetime) -> datetime:
    """When the ``window`` containing ``now`` began, by ``provider``'s clock."""
    now = now.astimezone(UTC)
    if window == "second":
        return now.replace(microsecond=0)
    if window == "minute":
        return now.replace(second=0, microsecond=0)
    if window == "hour":
        return now.replace(minute=0, second=0, microsecond=0)
    local = now.astimezone(DAY_ZONES.get(provider, UTC))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "month":
        start = start.replace(day=1)
    return start.astimezone(UTC)


def window_end(provider: UsageProvider, window: Window, now: datetime) -> datetime:
    """When the ``window`` containing ``now`` ends, and its limit resets."""
    start = window_start(provider, window, now)
    if window == "second":
        return start + timedelta(seconds=1)
    if window == "minute":
        return start + timedelta(minutes=1)
    if window == "hour":
        return start + timedelta(hours=1)
    if window == "day":
        return start + timedelta(days=1)
    local = start.astimezone(DAY_ZONES.get(provider, UTC))
    year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    return local.replace(year=year, month=month).astimezone(UTC)


def month_of(provider: UsageProvider, now: datetime) -> date:
    """The first day of the month containing ``now``, by ``provider``'s clock."""
    return window_start(provider, "month", now).astimezone(DAY_ZONES.get(provider, UTC)).date()


# What providers report in their answers.


@dataclass(frozen=True)
class ReportedLimit:
    """A limit and how much of it is left, as a provider's headers or account page say."""

    window: Window
    unit: Unit
    limit: int | None
    remaining: int | None
    resets_at: datetime | None
    used: int | None = None


@dataclass
class Reported:
    """What a provider last reported about a key's limits, and when."""

    at: datetime
    limits: list[ReportedLimit]


_reported: dict[tuple[str, str], Reported] = {}
"""(key id, provider) -> the provider's latest report, from its headers or account page."""


def _integer(value: str | None) -> int | None:
    try:
        return None if value is None else int(float(value))
    except ValueError:
        return None


def _rfc3339(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_headers(
    provider: UsageProvider, headers: Mapping[str, str], now: datetime
) -> list[ReportedLimit]:
    """The limits a provider's answer headers report, if it sends any."""
    lowered = {name.lower(): value for name, value in headers.items()}
    if provider in ("finnhub", "eodhd"):
        limit = _integer(lowered.get("x-ratelimit-limit"))
        remaining = _integer(lowered.get("x-ratelimit-remaining"))
        if limit is None and remaining is None:
            return []
        reset = _integer(lowered.get("x-ratelimit-reset"))
        resets_at = (
            datetime.fromtimestamp(reset, UTC)
            if reset is not None and reset > 1_000_000_000
            else window_end(provider, "minute", now)
        )
        return [ReportedLimit("minute", "requests", limit, remaining, resets_at)]
    if provider == "anthropic":
        found: list[ReportedLimit] = []
        for name, unit in (
            ("requests", "requests"),
            ("input-tokens", "input_tokens"),
            ("output-tokens", "output_tokens"),
        ):
            limit = _integer(lowered.get(f"anthropic-ratelimit-{name}-limit"))
            remaining = _integer(lowered.get(f"anthropic-ratelimit-{name}-remaining"))
            if limit is None and remaining is None:
                continue
            refill = _rfc3339(lowered.get(f"anthropic-ratelimit-{name}-reset"))
            found.append(
                ReportedLimit("minute", unit, limit, remaining, refill)  # type: ignore[arg-type]
            )
        return found
    return []


def note_report(provider: UsageProvider, api_key: str, limits: list[ReportedLimit]) -> None:
    """Keep what a provider reported about ``api_key``'s limits, merged with its earlier
    report: a header report replaces only the windows it covers."""
    if not limits:
        return
    entry = (key_id(api_key), provider)
    kept = _reported.get(entry)
    now = datetime.now(UTC)
    covered = {(limit.window, limit.unit) for limit in limits}
    earlier = (
        []
        if kept is None
        else [old for old in kept.limits if (old.window, old.unit) not in covered]
    )
    _reported[entry] = Reported(now, [*earlier, *limits])


def reported(provider: UsageProvider, api_key: str) -> Reported | None:
    """The provider's latest report on ``api_key``, if it is recent."""
    found = _reported.get((key_id(api_key), provider))
    if found is None or (datetime.now(UTC) - found.at).total_seconds() > REPORTED_FRESH_SECONDS:
        return None
    return found


# Counting PyQxel's own requests.


@dataclass
class _Pending:
    """Counts not yet written to the database."""

    hours: dict[tuple[str, str, datetime], list[int]] = field(
        default_factory=lambda: defaultdict(lambda: [0, 0])
    )
    symbols: set[tuple[str, str, date, str]] = field(default_factory=set)


_pending = _Pending()
_minutes: dict[tuple[str, str], tuple[int, int]] = {}
"""(key id, provider) -> (minute number, requests this process sent in it)."""
_flusher: asyncio.Task[None] | None = None
_flush_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None
"""The lock that keeps flushes apart, with the event loop it belongs to."""
_last_prune: float = 0.0
_tracked: ContextVar[dict[str, int] | None] = ContextVar("tracked_usage", default=None)


def record(
    provider: UsageProvider,
    api_key: str,
    *,
    symbols: Iterable[str] = (),
    size: int = 0,
    headers: Mapping[str, str] | None = None,
) -> None:
    """Count one request sent on ``api_key``, with the symbols it named and the bytes its
    answer took, and keep what the answer's headers report of the key's limits.

    It never waits or fails, so it can be called from any provider's request code; the
    count reaches the database at the next :func:`flush`.
    """
    now = datetime.now(UTC)
    kid = key_id(api_key)
    counts = _pending.hours[(kid, provider, window_start(provider, "hour", now))]
    counts[0] += 1
    counts[1] += max(0, size)
    if provider in SYMBOL_LIMITED:
        month = month_of(provider, now)
        for symbol in symbols:
            if symbol:
                _pending.symbols.add((kid, provider, month, symbol.strip().upper()[:32]))
    minute = int(time.time() // 60)
    last = _minutes.get((kid, provider))
    _minutes[kid, provider] = (minute, (last[1] if last and last[0] == minute else 0) + 1)
    tracked = _tracked.get()
    if tracked is not None:
        tracked[provider] = tracked.get(provider, 0) + 1
    if headers is not None:
        note_report(provider, api_key, parse_headers(provider, headers, now))
    _schedule_flush()


def minute_count(provider: UsageProvider, api_key: str) -> int:
    """Requests this process sent on ``api_key`` in the current minute."""
    last = _minutes.get((key_id(api_key), provider))
    return last[1] if last and last[0] == int(time.time() // 60) else 0


@contextmanager
def track() -> Iterator[dict[str, int]]:
    """Count the requests sent inside the block, by provider, as for one nightly refresh."""
    counts: dict[str, int] = {}
    token = _tracked.set(counts)
    try:
        yield counts
    finally:
        _tracked.reset(token)


def _schedule_flush() -> None:
    """Start the task that writes counts to the database, if a loop runs and none does."""
    global _flusher
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _flusher is not None and not _flusher.done() and _flusher.get_loop() is loop:
        return
    _flusher = loop.create_task(_flush_later())


async def _flush_later() -> None:
    await asyncio.sleep(FLUSH_SECONDS)
    await flush()


def _upsert(
    dialect: str, table: type, values: dict[str, object], add: dict[str, int] | None
) -> Any:
    """An insert that adds to the counts of a row already there, or skips a duplicate."""
    from sqlalchemy.dialects import postgresql, sqlite

    insert = postgresql.insert if dialect == "postgresql" else sqlite.insert
    statement: Any = insert(table).values(**values)
    if add is None:
        return statement.on_conflict_do_nothing()
    primary = table.__table__.primary_key.columns  # type: ignore[attr-defined]
    keys = [column.name for column in primary]
    return statement.on_conflict_do_update(
        index_elements=keys,
        set_={name: getattr(table, name) + statement.excluded[name] for name in add},
    )


async def flush() -> None:
    """Write the counts kept in memory to the database, adding to what is there.

    A database failure is logged and the counts are kept for the next try, so a blip
    loses nothing; usage is a convenience, so it never fails a request.
    """
    global _pending, _flush_lock, _last_prune
    from app.db.session import get_sessionmaker

    loop = asyncio.get_running_loop()
    if _flush_lock is None or _flush_lock[0] is not loop:
        _flush_lock = (loop, asyncio.Lock())
    async with _flush_lock[1]:
        pending, _pending = _pending, _Pending()
        if not pending.hours and not pending.symbols:
            return
        try:
            async with get_sessionmaker()() as session:
                dialect = session.get_bind().dialect.name
                for (kid, provider, hour), (requests, size) in pending.hours.items():
                    await session.execute(
                        _upsert(
                            dialect,
                            ProviderUsageRecord,
                            {
                                "key_id": kid,
                                "provider": provider,
                                "hour": hour,
                                "requests": requests,
                                "bytes": size,
                            },
                            {"requests": requests, "bytes": size},
                        )
                    )
                for kid, provider, month, symbol in pending.symbols:
                    await session.execute(
                        _upsert(
                            dialect,
                            ProviderSymbolRecord,
                            {"key_id": kid, "provider": provider, "month": month, "symbol": symbol},
                            None,
                        )
                    )
                if time.monotonic() - _last_prune > 6 * 60 * 60:
                    _last_prune = time.monotonic()
                    cutoff = datetime.now(UTC) - timedelta(days=KEEP_DAYS)
                    await session.execute(
                        delete(ProviderUsageRecord).where(ProviderUsageRecord.hour < cutoff)
                    )
                    await session.execute(
                        delete(ProviderSymbolRecord).where(
                            ProviderSymbolRecord.month < cutoff.date()
                        )
                    )
                await session.commit()
        except (SQLAlchemyError, IntegrityError, OSError) as exc:
            logger.warning("Could not save provider usage counts; will retry: %s", exc)
            for entry, (requests, size) in pending.hours.items():
                counts = _pending.hours[entry]
                counts[0] += requests
                counts[1] += size
            _pending.symbols |= pending.symbols
            _schedule_flush()


async def counted(provider: UsageProvider, api_key: str, since: datetime) -> tuple[int, int]:
    """Requests PyQxel sent on ``api_key`` since ``since``, a whole hour, and the bytes
    their answers took, across every instance."""
    from app.db.session import get_sessionmaker

    await flush()
    async with get_sessionmaker()() as session:
        row = (
            await session.execute(
                select(
                    func.coalesce(func.sum(ProviderUsageRecord.requests), 0),
                    func.coalesce(func.sum(ProviderUsageRecord.bytes), 0),
                ).where(
                    ProviderUsageRecord.key_id == key_id(api_key),
                    ProviderUsageRecord.provider == provider,
                    ProviderUsageRecord.hour >= since,
                )
            )
        ).one()
    return int(row[0]), int(row[1])


async def symbols_counted(provider: UsageProvider, api_key: str, month: date) -> set[str]:
    """The different symbols PyQxel asked for on ``api_key`` in ``month``."""
    from app.db.session import get_sessionmaker

    await flush()
    async with get_sessionmaker()() as session:
        rows = await session.scalars(
            select(ProviderSymbolRecord.symbol).where(
                ProviderSymbolRecord.key_id == key_id(api_key),
                ProviderSymbolRecord.provider == provider,
                ProviderSymbolRecord.month == month,
            )
        )
        return set(rows)


def clear_usage_memory() -> None:
    """Forget counts and reports kept in memory, for tests."""
    global _pending
    _pending = _Pending()
    _minutes.clear()
    _reported.clear()


# Reports for Settings.

DAY_RESETS: dict[UsageProvider, str] = {
    "tiingo": "Midnight US Eastern Standard Time, which is 1 AM during daylight saving time",
    "eodhd": "Midnight GMT",
    "fmp": "Once a day; FMP does not say when, so PyQxel counts from midnight UTC",
    "alpha_vantage": (
        "Once a day; Alpha Vantage does not say when, so PyQxel counts from midnight UTC"
    ),
    "finnhub": "Midnight UTC",
}

MONTH_RESETS: dict[UsageProvider, str] = {
    "tiingo": "The 1st of each month, at midnight US Eastern Standard Time",
}


def resets_text(provider: UsageProvider, window: Window) -> str:
    """When ``provider``'s limits over ``window`` reset, in words."""
    if window == "second":
        return "Every second"
    if window == "minute":
        return "Every minute"
    if window == "hour":
        return "Every hour"
    if window == "day":
        return DAY_RESETS.get(provider, "Midnight UTC")
    return MONTH_RESETS.get(provider, "The 1st of each month")


def _limit(
    provider: UsageProvider,
    window: Window,
    unit: Unit,
    limit: int | None,
    used: int | None,
    source: Literal["provider", "pyqxel"],
    now: datetime,
    resets_at: datetime | None = None,
    note: str | None = None,
    resets: str | None = None,
) -> UsageLimit:
    remaining = None if limit is None or used is None else max(0, limit - used)
    return UsageLimit(
        window=window,
        unit=unit,
        limit=limit,
        used=used,
        remaining=remaining,
        resets_at=resets_at or window_end(provider, window, now),
        resets=resets or resets_text(provider, window),
        source=source,
        note=note,
    )


EODHD_ACCOUNT_FIELDS: tuple[str, ...] = ("apiRequests", "apiRequestsDate", "dailyRateLimit")


def _reached_window(
    provider: UsageProvider, plan: Plan | None, until: datetime, now: datetime
) -> Window | None:
    """The window of ``plan`` whose limit a provider said was reached, judged by when PyQxel
    will ask again: the window ending then, or else the day, whose reset is unknown."""
    if plan is None:
        return None
    windows = [item.window for item in plan.limits if item.window in ("hour", "day", "month")]
    for window in windows:
        if abs((window_end(provider, window, now) - until).total_seconds()) <= 120:
            return window
    return "day" if "day" in windows else (windows[0] if windows else None)


async def provider_report(
    provider: UsageProvider,
    api_key: str,
    plan_id: str | None = None,
    eodhd_account: Mapping[str, object] | None = None,
    limit_reached_until: datetime | None = None,
) -> ProviderUsage:
    """A key's plan, each of its limits, and how much is used, from the provider's own
    figures where it reports them and from PyQxel's count where it does not.

    Args:
        provider: Whose key it is.
        api_key: The key.
        plan_id: The plan the user says the key is on; the free plan when not given.
        eodhd_account: For EODHD, the answer of its ``/api/user``, if it could be read.
        limit_reached_until: When PyQxel will ask the provider again, if it answered that
            the key's limit was reached. That answer counts use from anywhere, so the limit
            it names is shown as used up, as the provider's own figure.
    """
    now = datetime.now(UTC)
    name = PROVIDER_NAMES[provider]
    report = reported(provider, api_key)
    by_window = {} if report is None else {(r.window, r.unit): r for r in report.limits}
    limits: list[UsageLimit] = []
    if provider == "anthropic":
        for unit in ("requests", "input_tokens", "output_tokens"):
            found = by_window.get(("minute", unit))
            if found is None:
                continue
            used = (
                None
                if found.limit is None or found.remaining is None
                else max(0, found.limit - found.remaining)
            )
            limits.append(
                _limit(
                    provider,
                    "minute",
                    unit,
                    found.limit,
                    used,
                    "provider",
                    now,
                    resets_at=found.resets_at,
                    resets="Refills continuously, up to the limit a minute",
                )
            )
        day, _ = await counted(provider, api_key, window_start(provider, "day", now))
        limits.append(
            _limit(
                provider,
                "day",
                "requests",
                None,
                day,
                "pyqxel",
                now,
                resets="Counted from midnight UTC",
                note="PyQxel's own count of its requests to Claude today; Anthropic sets no "
                "daily limit, but its Console caps what you spend a month.",
            )
        )
        return ProviderUsage(
            provider=provider,
            name=name,
            plan=None,
            plan_name=None,
            plan_detected=True,
            plans=[],
            source=_overall(limits),
            limits=limits,
            reported_at=None if report is None else report.at,
            note=(
                "Anthropic sends these limits with each answer, so they appear after your "
                "first AI request and show the latest one. They refill continuously. Your "
                "spending and monthly limit are in Anthropic's Console."
                if report is not None
                else "Anthropic sends its limits with each answer: they appear here after "
                "your next AI request. Your spending and monthly limit are in Anthropic's "
                "Console."
            ),
        )

    plan = plan_for(provider, plan_id)
    detected = False
    account_day: tuple[int, int] | None = None
    extra: int | None = None
    if provider == "eodhd" and eodhd_account is not None:
        allowance = eodhd_account.get("dailyRateLimit")
        spent = eodhd_account.get("apiRequests")
        spent_on = str(eodhd_account.get("apiRequestsDate") or "")
        if isinstance(allowance, int | float) and isinstance(spent, int | float):
            today = window_start(provider, "day", now).date().isoformat()
            # EODHD keeps showing the last day used until the first request of a new day.
            account_day = (int(allowance), int(spent) if spent_on == today else 0)
            detected = True
            plan = plan_for(provider, "free" if allowance <= 20 else "paid")
        bonus = eodhd_account.get("extraLimit")
        if isinstance(bonus, int | float) and bonus > 0:
            extra = int(bonus)
    reached = (
        None
        if limit_reached_until is None
        else _reached_window(provider, plan, limit_reached_until, now)
    )
    for item in () if plan is None else plan.limits:
        start = window_start(provider, item.window, now)
        if item.window == reached and item.unit in ("requests", "calls"):
            assert limit_reached_until is not None
            limits.append(
                _limit(
                    provider,
                    item.window,
                    item.unit,
                    item.limit,
                    item.limit,
                    "provider",
                    now,
                    note=f"{name} answered that this limit is reached, counting the key's use "
                    "from anywhere. PyQxel asks it again at "
                    f"{limit_reached_until.astimezone(UTC):%H:%M} UTC.",
                )
            )
            continue
        found = by_window.get((item.window, "requests"))
        if item.window in ("second", "minute"):
            if found is not None and found.remaining is not None:
                limit = found.limit if found.limit is not None else item.limit
                limits.append(
                    _limit(
                        provider,
                        item.window,
                        item.unit,
                        limit,
                        max(0, limit - found.remaining),
                        "provider",
                        now,
                        resets_at=found.resets_at,
                    )
                )
                if found.limit is not None and found.limit != item.limit:
                    detected = True
            elif item.window == "minute":
                limits.append(
                    _limit(
                        provider,
                        item.window,
                        item.unit,
                        item.limit,
                        minute_count(provider, api_key),
                        "pyqxel",
                        now,
                    )
                )
            continue
        if provider == "eodhd" and item.window == "day" and account_day is not None:
            limits.append(
                _limit(
                    provider,
                    "day",
                    "calls",
                    account_day[0],
                    account_day[1],
                    "provider",
                    now,
                    note=(
                        None
                        if extra is None
                        else f"Plus {extra:,} extra calls, used once the day's are gone; "
                        "they do not expire."
                    ),
                )
            )
            continue
        if item.unit == "symbols":
            used = len(await symbols_counted(provider, api_key, month_of(provider, now)))
        else:
            requests, size = await counted(provider, api_key, start)
            used = size if item.unit == "bytes" else requests
        limits.append(_limit(provider, item.window, item.unit, item.limit, used, "pyqxel", now))
    choices = [
        PlanChoice(id=choice.id, name=choice.name, verified=choice.verified)
        for choice in PLANS.get(provider, ())
    ]
    source = _overall(limits)
    note = None
    if source != "provider":
        note = (
            f"{name} does not report usage, so these figures count only PyQxel's own "
            f"requests on this key. Requests sent from elsewhere with the same key are not "
            f"counted; {name}'s own dashboard shows them."
            if source == "pyqxel"
            else f"{name} reports some of these figures; the ones marked as PyQxel's count "
            "only PyQxel's own requests."
        )
    if plan is not None and not plan.verified:
        note = (note + " " if note else "") + (
            f"The {plan.name} plan's limits come from third-party listings, since {name}'s "
            "pricing page could not be read."
        )
    return ProviderUsage(
        provider=provider,
        name=name,
        plan=None if plan is None else plan.id,
        plan_name=None if plan is None else plan.name,
        plan_detected=detected,
        plans=[] if detected else choices,
        source=source,
        limits=limits,
        reported_at=None if report is None else report.at,
        note=note,
    )


def _overall(limits: list[UsageLimit]) -> Literal["provider", "pyqxel", "mixed", "none"]:
    sources = {limit.source for limit in limits if limit.used is not None}
    if not sources:
        return "none"
    if sources == {"provider"}:
        return "provider"
    if sources == {"pyqxel"}:
        return "pyqxel"
    return "mixed"


def tightest(report: ProviderUsage, unit: str = "requests") -> UsageLimit | None:
    """The limit counting ``unit`` (or EODHD's calls) with the smallest share left."""
    candidates = [
        limit
        for limit in report.limits
        if limit.unit in (unit, "calls") and limit.limit and limit.remaining is not None
    ]
    return min(
        candidates, key=lambda limit: (limit.remaining or 0) / (limit.limit or 1), default=None
    )
