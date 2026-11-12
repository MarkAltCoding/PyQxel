"""Storage of nightly screener refreshes: who opted in, their encrypted keys, their runs,
and the universe each one built.

Opting in is the only way PyQxel stores provider keys. They are encrypted together as one
JSON object (:func:`app.core.security.encrypt_secret`) and decrypted only by the refresh
job, never to answer a request. Opting out deletes them at once, as deleting the account
does (:func:`app.db.users.delete_user`).
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast, get_args
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.credentials import ProviderKeys
from app.core.security import CredentialsUnavailableError, decrypt_secret, encrypt_secret
from app.db.billing import refresh_paid_for
from app.db.tables import (
    NightlyRefreshRecord,
    ScreenerListingRecord,
    UserScreenerRunRecord,
    UserScreenerStockRecord,
    as_utc,
)
from app.models.nightly import (
    FREE_PLAN_HISTORIES,
    PRICE_KEY_ORDER,
    STORED_PROVIDERS,
    PriceKey,
    NightlyProvider,
    NightlyRefreshSettings,
    NightlyKeyUse,
    NightlyRefreshStatus,
    NightlyRun,
    NightlyTimeSlot,
    EVERYTHING,
    NightlyUniverse,
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
class UniverseChoice:
    """The sets of securities a refresh rebuilds, combined, and the tickers it adds."""

    universes: tuple[NightlyUniverse, ...] = tuple(EVERYTHING)
    custom_symbols: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, list[str]]:
        """The choice as stored with a run."""
        return {"universes": list(self.universes), "custom_symbols": list(self.custom_symbols)}

    @classmethod
    def of(cls, record: NightlyRefreshRecord) -> "UniverseChoice":
        """The choice stored with ``record``, leaving out any set this version does not know."""
        known = set(get_args(NightlyUniverse))
        return cls(
            universes=tuple(
                cast(NightlyUniverse, name) for name in (record.universes or []) if name in known
            ),
            custom_symbols=tuple(record.custom_symbols or []),
        )


@dataclass(frozen=True)
class DueRefresh:
    """A user whose refresh is due, and the keys it runs on."""

    user_id: str
    keys: ProviderKeys
    price_history_limits: dict[PriceKey, int | None] = field(default_factory=dict)
    """Most price histories each price-history key fetches; ``None`` for as many as its
    plan allows."""
    etfs_scanned_at: datetime | None = None
    """When the refresh last checked every US ETF; ``None`` if never."""
    choice: UniverseChoice = field(default_factory=UniverseChoice)
    """What the refresh rebuilds."""


PriceLimits = dict[PriceKey, int | None]
"""Most price histories each price-history key fetches a night; ``None`` for its plan's most."""


def price_limits(providers: Iterable[str], chosen: Mapping[Any, int | None] | None) -> PriceLimits:
    """The limit of each price-history key among ``providers``: the one ``chosen``, or its
    free plan's allowance where none was."""
    chosen = chosen or {}
    providers = set(providers)
    return {
        key: (chosen[key] if key in chosen else FREE_PLAN_HISTORIES[key])
        for key in PRICE_KEY_ORDER
        if key in providers
    }


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
    if settings.use_keys is not None:
        providers = [provider for provider in providers if provider in settings.use_keys]
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
    record.price_history_limits = {
        str(key): limit
        for key, limit in price_limits(providers, settings.price_history_limits).items()
    }
    record.universes = list(settings.universes)
    record.custom_symbols = list(settings.custom_symbols)
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
        usage=record.usage,
        notes=record.notes or [],
    )


ASSUMED_LISTINGS: int = 5_000
"""Listed stocks assumed before the shared SEC stage has stored its list."""
ASSUMED_PRICED_STOCKS: int = 2_000
"""Stocks above the market cap floor, assumed before a first refresh has counted them."""
INDEX_LISTINGS: dict[NightlyUniverse, int] = {"sp500": 545, "nasdaq100": 115}
"""About how many listings an index's companies have, each asked of Finnhub: its notes and
preferreds too, until Finnhub's figures show which are its common shares."""
SP500_ON_NASDAQ: int = 100
"""About how many of the S&P 500's listings are on Nasdaq, so already among its stocks."""
NASDAQ100_IN_SP500: int = 90
"""About how many of the Nasdaq-100's listings are in the S&P 500 as well."""
ASSUMED_INTERNATIONAL: int = 700
"""About how many foreign companies' stocks are listed in the US."""
ASSUMED_ETF_REQUESTS: int = 4_600
"""Finnhub requests for every US ETF: each one's metrics and, without price history, a
quote."""
INDEX_STOCKS: dict[NightlyUniverse, int] = {"sp500": 500, "nasdaq100": 100, "international": 400}
"""About how many stocks a set keeps above the floors, for the price-history estimate."""


async def _finnhub_requests(session: AsyncSession, choice: UniverseChoice) -> int:
    """About how many Finnhub requests a refresh of ``choice`` sends: one per security for
    its metrics, counting a security in several sets once."""
    chosen = set(choice.universes)
    if "us_stocks" in chosen:
        listings = int(
            await session.scalar(select(func.count()).select_from(ScreenerListingRecord)) or 0
        )
        stocks = listings or ASSUMED_LISTINGS
    else:
        # Every set but the US's is part of it, so the others overlap less; this counts the
        # overlaps it knows of once.
        stocks = 0
        if "nasdaq" in chosen:
            nasdaq = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ScreenerListingRecord)
                    .where(ScreenerListingRecord.exchange == "Nasdaq")
                )
                or 0
            )
            stocks += nasdaq or ASSUMED_LISTINGS // 2
        if "sp500" in chosen:
            stocks += INDEX_LISTINGS["sp500"] - (SP500_ON_NASDAQ if "nasdaq" in chosen else 0)
        if "nasdaq100" in chosen and "nasdaq" not in chosen:
            stocks += INDEX_LISTINGS["nasdaq100"] - (NASDAQ100_IN_SP500 if "sp500" in chosen else 0)
        if "international" in chosen:
            stocks += ASSUMED_INTERNATIONAL
    etfs = ASSUMED_ETF_REQUESTS if "us_etfs" in chosen else 0
    return stocks + etfs + len(choice.custom_symbols)


def _priced_stocks(choice: UniverseChoice) -> int:
    """About how many stocks a refresh of ``choice`` keeps, before one has counted them."""
    chosen = set(choice.universes)
    if chosen & {"us_stocks", "nasdaq"}:
        return ASSUMED_PRICED_STOCKS
    return sum(INDEX_STOCKS.get(name, 0) for name in chosen) + len(choice.custom_symbols)


def run_choice(choice: UniverseChoice, limits: PriceLimits) -> dict[str, Any]:
    """What a run rebuilt and with which limits, as stored with it (``choice``)."""
    return {**choice.as_dict(), "price_history_limits": dict(limits)}


async def key_use(
    session: AsyncSession,
    providers: list[NightlyProvider],
    limits: PriceLimits,
    success: UserScreenerRunRecord | None,
    choice: UniverseChoice | None = None,
) -> list[NightlyKeyUse]:
    """About how many requests one refresh of ``choice`` sends on each stored key.

    Finnhub gets one request per security for its metrics, once however many of the sets
    chosen hold it. Price histories go to the price-history keys, one request per stock,
    largest first, the cheapest key first, each taking up to its own limit in ``limits``
    (all that are left for a key whose plan sets the limit). The latest successful
    refresh's own counts are used only if it ran this very choice, with these limits.
    """
    choice = choice or UniverseChoice()
    limits = price_limits(providers, dict(limits))
    same = success is not None and success.choice == run_choice(choice, limits)
    stocks = success.stocks if same and success is not None and success.stocks else None
    left = stocks or _priced_stocks(choice)
    counts = success.usage if same and success is not None and success.usage else {}
    found: list[NightlyKeyUse] = []
    if "finnhub" in providers:
        found.append(
            NightlyKeyUse(
                provider="finnhub",
                requests=counts.get("finnhub") or await _finnhub_requests(session, choice),
                detail="One request per stock or ETF for its metrics, paced under the "
                "plan's limit a minute (60 on the free plan), plus a quote for each with no "
                "other price.",
            )
        )
    for provider, limit in limits.items():
        take = counts.get(provider, left if limit is None else min(left, limit))
        found.append(
            NightlyKeyUse(
                provider=provider,
                requests=take,
                detail=(
                    "One request per stock given price-history columns, largest first, "
                    + ("until its plan stops it." if limit is None else f"up to {limit:,}.")
                ),
            )
        )
        left = max(0, left - take)
    return found


NIGHTLY_CHECK_MINUTE: int = 7
"""The minute past each UTC hour at which Cloud Scheduler asks the service whether any
refresh is due (``NIGHTLY_SCHEDULE`` in ``deploy/cloud-run.sh``; a test checks they agree).
Every refresh whose time came since the check before starts in the same job."""


def check_for(moment: datetime) -> datetime:
    """The first hourly check at or after ``moment``, which starts a refresh due then."""
    check = moment.astimezone(timezone.utc).replace(
        minute=NIGHTLY_CHECK_MINUTE, second=0, microsecond=0
    )
    return check if check >= moment else check + timedelta(hours=1)


async def time_slots(session: AsyncSession, user_id: str, now: datetime) -> list[NightlyTimeSlot]:
    """The next 24 hourly checks, with how many refreshes of users other than ``user_id``
    each will start: those turned on and paid for (:func:`refresh_paid_for`), at their
    next scheduled time."""
    first = check_for(now)
    counts = {first + timedelta(hours=hour): 0 for hour in range(24)}
    for record in list(await session.scalars(select(NightlyRefreshRecord))):
        if record.user_id == user_id or not await refresh_paid_for(session, record.user_id):
            continue
        today = now.astimezone(ZoneInfo(record.time_zone)).date()
        moment = scheduled_moment(record.refresh_time, record.time_zone, today)
        if check_for(moment) < first:
            moment = scheduled_moment(
                record.refresh_time, record.time_zone, today + timedelta(days=1)
            )
        check = check_for(moment)
        if check in counts:
            counts[check] += 1
    return [NightlyTimeSlot(checks_at=check, others=others) for check, others in counts.items()]


Preview = tuple[list[NightlyProvider], PriceLimits, UniverseChoice]
"""Keys, their price-history limits, and sets and tickers the user is considering."""


async def refresh_status(
    session: AsyncSession,
    user_id: str,
    available: bool,
    now: datetime | None = None,
    preview: Preview | None = None,
) -> NightlyRefreshStatus:
    """Whether ``user_id`` opted in, what is stored, and how their refreshes went.

    With ``preview``, keys, a price-history cap, and sets and tickers the user is
    considering, ``key_use`` says what one refresh would send on each of those keys
    instead of the stored ones.
    """
    record = await session.get(NightlyRefreshRecord, user_id)
    latest = await _runs(session, user_id)
    success = await latest_success(session, user_id)
    stored = None if record is None else UniverseChoice.of(record)
    status = NightlyRefreshStatus(
        available=available,
        enabled=record is not None,
        latest_run=_run(latest[0]) if latest else None,
        last_success=None if success is None else _run(success),
    )
    if available:
        status.time_slots = await time_slots(session, user_id, now or datetime.now(timezone.utc))
    if preview is not None:
        status.key_use = await key_use(session, preview[0], preview[1], success, preview[2])
    if record is None or stored is None:
        return status
    started = as_utc(latest[0].started_at) if latest else None
    return status.model_copy(
        update={
            "refresh_time": record.refresh_time,
            "time_zone": record.time_zone,
            "providers": [p for p in record.providers if p in STORED_PROVIDERS],
            "price_history_limits": price_limits(
                list(record.providers), record.price_history_limits
            ),
            "universes": list(stored.universes),
            "custom_symbols": list(stored.custom_symbols),
            "key_use": (
                status.key_use
                if preview is not None
                else await key_use(
                    session,
                    [p for p in record.providers if p in STORED_PROVIDERS],
                    price_limits(list(record.providers), record.price_history_limits),
                    success,
                    stored,
                )
            ),
            "keys_updated_at": as_utc(record.keys_updated_at),
            "next_run_at": next_run_at(
                record.refresh_time,
                record.time_zone,
                now or datetime.now(timezone.utc),
                started,
            ),
        }
    )


def _is_due(
    record: NightlyRefreshRecord, last: UserScreenerRunRecord | None, now: datetime
) -> bool:
    """Whether ``record``'s refresh is due at ``now``, given its user's last run: its time
    has come since that run started, and that run is not still running, or has run longer
    than :data:`STALE_AFTER` and so is taken to have stopped."""
    if last is not None and last.status == RUNNING and now - as_utc(last.started_at) < STALE_AFTER:
        return False
    started = None if last is None else as_utc(last.started_at)
    return next_run_at(record.refresh_time, record.time_zone, now, started) <= now


async def any_due(session: AsyncSession, now: datetime) -> bool:
    """Whether any user's refresh is due at ``now``, changing nothing: the hourly check
    that starts the refresh job only when it has work to do."""
    for record in list(await session.scalars(select(NightlyRefreshRecord))):
        runs = await _runs(session, record.user_id)
        if _is_due(record, runs[0] if runs else None, now) and await refresh_paid_for(
            session, record.user_id
        ):
            return True
    return False


async def due_refreshes(session: AsyncSession, now: datetime) -> list[DueRefresh]:
    """The users whose refresh is due at ``now``, with their decrypted keys.

    A user whose last run is still running is skipped, unless it started more than
    :data:`STALE_AFTER` ago, when it is recorded as failed and the user is due again.
    A user whose keys cannot be decrypted gets a failed run saying so. A user no one is
    paying for (:func:`app.db.billing.refresh_paid_for`) is left out until they pay.
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
        if not _is_due(record, last, now):
            continue
        if not await refresh_paid_for(session, record.user_id):
            # Waits, with no run recorded, until the user adds credit or a subscription;
            # Settings says so.
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
        due.append(
            DueRefresh(
                user_id=record.user_id,
                keys=keys,
                price_history_limits=price_limits(
                    list(record.providers), record.price_history_limits
                ),
                etfs_scanned_at=(
                    None if record.etfs_scanned_at is None else as_utc(record.etfs_scanned_at)
                ),
                choice=UniverseChoice.of(record),
            )
        )
    return due


async def mark_etfs_scanned(session: AsyncSession, user_id: str, when: datetime) -> None:
    """Record that ``user_id``'s refresh checked every US ETF at ``when``."""
    record = await session.get(NightlyRefreshRecord, user_id)
    if record is not None:
        record.etfs_scanned_at = when
        await session.commit()


async def user_etfs(session: AsyncSession, user_id: str) -> list[tuple[str, str, str]]:
    """The symbol, name, and exchange of each ETF in ``user_id``'s own universe."""
    rows = await session.execute(
        select(
            UserScreenerStockRecord.symbol,
            UserScreenerStockRecord.name,
            UserScreenerStockRecord.exchange,
        ).where(
            UserScreenerStockRecord.user_id == user_id,
            UserScreenerStockRecord.asset_type == "etf",
        )
    )
    return [(symbol, name, exchange) for symbol, name, exchange in rows]


async def start_run(
    session: AsyncSession,
    user_id: str,
    now: datetime | None = None,
    choice: dict[str, Any] | None = None,
) -> str:
    """Record ``user_id``'s refresh as started, with what it rebuilds (``choice``, from
    :func:`run_choice`); return its ID."""
    run_id = str(uuid4())
    session.add(
        UserScreenerRunRecord(
            id=run_id,
            user_id=user_id,
            started_at=now or datetime.now(timezone.utc),
            status=RUNNING,
            choice=choice,
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
    usage: dict[str, int] | None = None,
    notes: list[str] | None = None,
    cost_micros: int | None = None,
) -> None:
    """Record a user's refresh as failed with ``error``, or succeeded with its results,
    the requests it sent on each key, what it could not do as asked (``notes``), and what
    it cost to compute (``cost_micros``)."""
    run = await session.get(UserScreenerRunRecord, run_id)
    if run is None:
        return
    run.finished_at = datetime.now(timezone.utc)
    run.status = FAILED if error else SUCCEEDED
    run.error = error
    run.stocks = stocks
    run.priced = priced
    run.prices_as_of = prices_as_of
    run.usage = usage or None
    run.notes = notes or None
    run.cost_micros = cost_micros
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
