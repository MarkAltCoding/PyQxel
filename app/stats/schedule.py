"""When a scheduled refresh runs: the days a schedule picks, the time on each, and the next
moment after the last run; and the days the New York Stock Exchange is open.

The NYSE's holidays follow fixed rules (its Rule 7.2), so they are worked out here rather
than fetched: the hourly check that starts refreshes then needs no key. A holiday on a
Saturday is observed the Friday before, except New Year's Day, whose Friday would fall in
the year before, so it is not moved; one on a Sunday is observed the Monday after. Good
Friday follows Easter. Days the exchange closes for a one-off event, such as a national
day of mourning, cannot be known ahead and are taken as open.
"""

from datetime import date, datetime, time, timedelta, timezone
from functools import cache
from zoneinfo import ZoneInfo

from app.models.schedule import RefreshSchedule

LOOK_AHEAD_DAYS: int = 400
"""Furthest ahead the next run is looked for: past a year, so a yearly gap still has one."""


def _easter(year: int) -> date:
    """Easter Sunday in the Gregorian calendar (the anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = (h + m - 7 * n + 114) % 31 + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th ``weekday`` (Monday 0) of the month; ``n`` of -1 is the last."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date, move_saturday: bool = True) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1) if move_saturday else day
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


@cache
def nyse_holidays(year: int) -> frozenset[date]:
    """The days in ``year`` the NYSE is closed for a holiday, as observed."""
    days = {
        _observed(date(year, 1, 1), move_saturday=False),
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter(year) - timedelta(days=2),  # Good Friday
        _nth_weekday(year, 5, 0, -1),  # Memorial Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving Day
        _observed(date(year, 7, 4)),
        _observed(date(year, 12, 25)),
    }
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth
    return frozenset(day for day in days if day.year == year)


def is_trading_day(day: date) -> bool:
    """Whether the NYSE is open on ``day``: a weekday that is not one of its holidays."""
    return day.weekday() < 5 and day not in nyse_holidays(day.year)


def _last_day(day: date) -> int:
    following = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    return (following - timedelta(days=1)).day


def runs_on(schedule: RefreshSchedule, day: date) -> bool:
    """Whether ``schedule`` picks ``day`` (a day in its own time zone). For
    ``trading_days`` this is whether the NYSE is open that day; when it runs also depends
    on the time, as :func:`scheduled_at` decides."""
    match schedule.frequency:
        case "nightly":
            return True
        case "trading_days":
            return is_trading_day(day)
        case "weekdays":
            return day.weekday() < 5
        case "weekly":
            return day.weekday() == schedule.weekday
        case "monthly":
            wanted = schedule.month_day
            last = _last_day(day)
            return day.day == (last if wanted == -1 else min(wanted, last))
        case "dates":
            return day in set(schedule.dates)


def time_on(schedule: RefreshSchedule, base_time: str, day: date) -> str:
    """The time, HH:MM, ``schedule`` runs at on ``day``: its own for that weekday, if it
    gives one, else ``base_time``."""
    return schedule.times.get(day.weekday(), base_time)


def moment_on(schedule: RefreshSchedule, base_time: str, time_zone: str, day: date) -> datetime:
    """When on ``day`` the schedule runs, as aware UTC."""
    hour, minute = (int(part) for part in time_on(schedule, base_time, day).split(":"))
    local = datetime.combine(day, time(hour, minute), tzinfo=ZoneInfo(time_zone))
    return local.astimezone(timezone.utc)


NYSE_ZONE: ZoneInfo = ZoneInfo("America/New_York")

SESSION_READY_HOUR: int = 18
"""The hour in New York a trading day's closing prices are taken to be ready: two hours
after the 4 p.m. close, as :data:`app.data.fetcher.DAILY_BAR_READY_HOUR` takes them."""


SESSION_OPEN: time = time(9, 30)
"""When an NYSE session opens, New York time."""

REGULAR_CLOSE: time = time(16)
EARLY_CLOSE: time = time(13)
"""When the NYSE closes early: the day after Thanksgiving, and July 3 and December 24
when they fall Monday to Thursday (a Friday one is the observed holiday itself)."""

AFTER_HOURS_END: time = time(20)
"""When US after-hours trading ends, New York time."""


def early_closes(year: int) -> frozenset[date]:
    """The days in ``year`` the NYSE closes at 1 p.m."""
    days = {_nth_weekday(year, 11, 3, 4) + timedelta(days=1)}
    for month, day in ((7, 3), (12, 24)):
        candidate = date(year, month, day)
        if candidate.weekday() < 4:
            days.add(candidate)
    return frozenset(days)


def session_open(day: date) -> datetime:
    """When ``day``'s NYSE session opens, in New York time."""
    return datetime.combine(day, SESSION_OPEN, tzinfo=NYSE_ZONE)


def session_close(day: date) -> datetime:
    """When ``day``'s NYSE session closes, in New York time: 4 p.m., or 1 p.m. on an early
    close."""
    closes = EARLY_CLOSE if day in early_closes(day.year) else REGULAR_CLOSE
    return datetime.combine(day, closes, tzinfo=NYSE_ZONE)


def holding_window(now: datetime, days: int) -> tuple[datetime, datetime]:
    """When a holding of ``days`` calendar days bought at ``now`` begins and ends, in New
    York time.

    It begins now while the NYSE is open, or else at the next session's open. Bought at a
    later open, it ends at the close of the last session before the same day of the week
    ``days`` later, so a week chosen over a weekend runs from Monday's open to Friday's
    close: one full trading week. Bought during a session, it ends at the close ``days``
    later, or the last session before. Holidays and early closes are kept.
    """
    local = now.astimezone(NYSE_ZONE)
    today = local.date()
    if is_trading_day(today) and session_open(today) <= local < session_close(today):
        starts, entry, inclusive = local, today, True
    else:
        # Before today's open it is bought at that open; after the close, at the next one.
        entry = today
        if not (is_trading_day(today) and local < session_open(today)):
            entry = today + timedelta(days=1)
            while not is_trading_day(entry):
                entry += timedelta(days=1)
        starts, inclusive = session_open(entry), False
    last = entry + timedelta(days=max(1, days))
    if not inclusive:
        last -= timedelta(days=1)
    while last > entry and not is_trading_day(last):
        last -= timedelta(days=1)
    return starts, session_close(last)


def session_ready(day: date) -> datetime:
    """When ``day``'s NYSE session's prices are ready, as UTC."""
    return datetime.combine(day, time(SESSION_READY_HOUR), tzinfo=NYSE_ZONE).astimezone(
        timezone.utc
    )


def scheduled_at(
    schedule: RefreshSchedule, base_time: str, time_zone: str, day: date
) -> datetime | None:
    """When on ``day`` (in its own time zone) the schedule runs, or ``None`` if not that day.

    ``trading_days`` runs once after each NYSE session: at the day's time only if some
    trading day's prices became ready since the time the day before, so whatever the time
    zone and time of day, every session is picked up once, and no run repeats a weekend or
    holiday with nothing new. With a time before the prices are ready, as early morning in
    New York, each run takes the session before; in Tokyo, Saturday evening takes Friday's.
    """
    moment = moment_on(schedule, base_time, time_zone, day)
    if schedule.frequency != "trading_days":
        return moment if runs_on(schedule, day) else None
    previous = moment_on(schedule, base_time, time_zone, day - timedelta(days=1))
    session = previous.astimezone(NYSE_ZONE).date() - timedelta(days=1)
    while session <= moment.astimezone(NYSE_ZONE).date():
        if is_trading_day(session) and previous < session_ready(session) <= moment:
            return moment
        session += timedelta(days=1)
    return None


def next_moment(
    schedule: RefreshSchedule,
    base_time: str,
    time_zone: str,
    now: datetime,
    last_started: datetime | None,
) -> datetime | None:
    """When the schedule next runs: its first moment from today on that comes after the
    last run started. A moment already past today with no run since is due at once, so a
    schedule turned on in the evening runs that night. ``None`` when it never runs again,
    as when every chosen date has passed."""
    today = now.astimezone(ZoneInfo(time_zone)).date()
    for offset in range(LOOK_AHEAD_DAYS):
        moment = scheduled_at(schedule, base_time, time_zone, today + timedelta(days=offset))
        if moment is not None and (last_started is None or moment > last_started):
            return moment
    return None


DEFAULT_REFRESH_MINUTES: int = 120
"""How long a screener refresh is taken to run before one has finished to say."""

CONFLICT_DAYS: int = 62
"""How far ahead scheduled runs are checked against each other: about two months."""


def moments(
    schedule: RefreshSchedule, base_time: str, time_zone: str, now: datetime, days: int
) -> list[datetime]:
    """Every moment the schedule runs from today through ``days`` days ahead, as UTC."""
    today = now.astimezone(ZoneInfo(time_zone)).date()
    found = (
        scheduled_at(schedule, base_time, time_zone, today + timedelta(days=offset))
        for offset in range(days)
    )
    return [moment for moment in found if moment is not None]


def overlaps(
    refresh_starts: list[datetime], refresh_minutes: int, picks: list[datetime]
) -> list[tuple[datetime, datetime]]:
    """Each picks moment that falls inside a refresh, with that refresh's start: a refresh
    runs from its start until about ``refresh_minutes`` later, plus up to an hour before
    the hourly check that begins it."""
    found = []
    length = timedelta(minutes=refresh_minutes) + timedelta(hours=1)
    for pick in picks:
        for start in refresh_starts:
            if start <= pick < start + length:
                found.append((pick, start))
                break
    return found
