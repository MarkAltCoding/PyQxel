"""When scheduled refreshes run, and the NYSE's trading days."""

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.schedule import RefreshSchedule
from app.stats.schedule import is_trading_day, next_moment, nyse_holidays, runs_on, scheduled_at


def test_the_nyse_holidays_follow_its_rules() -> None:
    assert sorted(nyse_holidays(2026)) == [
        date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
        date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
        date(2026, 11, 26), date(2026, 12, 25),
    ]  # fmt: skip
    # 2027: Juneteenth and Christmas on Saturdays move to Friday, July 4 on Sunday to Monday.
    assert {date(2027, 6, 18), date(2027, 7, 5), date(2027, 12, 24)} <= nyse_holidays(2027)
    # New Year's Day on a Saturday is not moved into the year before.
    assert date(2021, 12, 31) not in nyse_holidays(2021) and is_trading_day(date(2021, 12, 31))
    assert not is_trading_day(date(2026, 11, 26)) and is_trading_day(date(2026, 11, 27))


def test_each_frequency_picks_its_days() -> None:
    saturday = date(2026, 11, 28)
    assert runs_on(RefreshSchedule(frequency="weekly", weekday=5), saturday)
    last = RefreshSchedule(frequency="monthly", month_day=-1)
    assert runs_on(last, date(2026, 11, 30)) and not runs_on(last, date(2026, 11, 29))
    # The 31st in a month of 30 days is its last day.
    assert runs_on(RefreshSchedule(frequency="monthly", month_day=31), date(2026, 11, 30))
    chosen = RefreshSchedule(frequency="dates", dates=[date(2026, 12, 3)])
    assert runs_on(chosen, date(2026, 12, 3)) and not runs_on(chosen, date(2026, 12, 4))


def test_the_next_run_follows_the_last_one_at_each_days_time() -> None:
    schedule = RefreshSchedule(frequency="nightly", times={5: "08:00"})
    now = datetime(2026, 11, 27, 23, 30, tzinfo=timezone.utc)  # Friday, 18:30 in New York
    # Today's 19:00 has not come.
    assert next_moment(schedule, "19:00", "America/New_York", now, None) == datetime(
        2026, 11, 28, 0, 0, tzinfo=timezone.utc
    )
    # Run tonight already: Saturday's own time, 08:00.
    ran = datetime(2026, 11, 28, 0, 5, tzinfo=timezone.utc)
    assert next_moment(schedule, "19:00", "America/New_York", now, ran) == datetime(
        2026, 11, 28, 13, 0, tzinfo=timezone.utc
    )
    past = RefreshSchedule(frequency="dates", dates=[date(2026, 11, 1)])
    assert next_moment(past, "19:00", "America/New_York", now, None) is None


def test_impossible_days_and_times_are_refused() -> None:
    with pytest.raises(ValidationError):
        RefreshSchedule(times={7: "08:00"})
    with pytest.raises(ValidationError):
        RefreshSchedule(times={1: "25:00"})
    with pytest.raises(ValidationError):
        RefreshSchedule(frequency="monthly", month_day=0)


def _runs(time: str, zone: str, days: range) -> list[str]:
    """The days of late November 2026 a trading-days schedule runs on at ``time``."""
    schedule = RefreshSchedule(frequency="trading_days")
    return [
        f"{d:%a %d}"
        for day in days
        if scheduled_at(schedule, time, zone, d := date(2026, 11, day)) is not None
    ]


def test_trading_days_run_once_after_each_session_whatever_the_time_and_zone() -> None:
    # Thanksgiving (Thursday the 26th) is closed; the weekend after it is the 28th and 29th.
    # An evening in New York: each session's own evening, none on the closed days.
    assert _runs("19:00", "America/New_York", range(23, 31)) == [
        "Mon 23", "Tue 24", "Wed 25", "Fri 27", "Mon 30",
    ]  # fmt: skip
    # Early morning in New York: each run takes the session before, so Saturday takes
    # Friday's and Friday takes Wednesday's, and Monday has nothing new.
    assert _runs("02:00", "America/New_York", range(23, 31)) == [
        "Tue 24", "Wed 25", "Thu 26", "Sat 28",
    ]  # fmt: skip
    # Saturday evening in Tokyo is Saturday morning in New York, just after Friday's close.
    assert "Sat 28" in _runs("19:00", "Asia/Tokyo", range(23, 31))
    assert "Fri 27" not in _runs("19:00", "Asia/Tokyo", range(23, 31))
