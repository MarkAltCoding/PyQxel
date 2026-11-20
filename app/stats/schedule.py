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
    """Whether ``schedule`` picks ``day`` (a day in its own time zone)."""
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
        day = today + timedelta(days=offset)
        if not runs_on(schedule, day):
            continue
        moment = moment_on(schedule, base_time, time_zone, day)
        if last_started is None or moment > last_started:
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
    return [
        moment_on(schedule, base_time, time_zone, today + timedelta(days=offset))
        for offset in range(days)
        if runs_on(schedule, today + timedelta(days=offset))
    ]


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
