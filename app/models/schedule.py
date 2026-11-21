"""How often a scheduled refresh runs: the screener's nightly refresh, or Claude's Picks."""

import re
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

Frequency = Literal["nightly", "trading_days", "weekdays", "weekly", "monthly", "dates"]
"""How often a refresh runs, at most once a day:

* ``nightly``: every day.
* ``trading_days`` (the default): once after each New York Stock Exchange session, so
  each refresh has a new day of prices and none repeats a weekend or holiday; whatever the
  time zone and time of day (:func:`app.stats.schedule.scheduled_at`).
* ``weekdays``: Monday to Friday, holidays included.
* ``weekly``: one day of the week, ``weekday``.
* ``monthly``: one day of the month, ``month_day``.
* ``dates``: the days listed in ``dates`` alone.
"""

TIME_PATTERN: str = r"^([01]\d|2[0-3]):[0-5]\d$"


class RefreshSchedule(BaseModel):
    """Which days a refresh runs on, and at what time on each."""

    frequency: Frequency = Field(default="trading_days", description="How often it runs.")
    weekday: int = Field(
        default=6,
        ge=0,
        le=6,
        description="For ``weekly``: the day of the week, Monday 0 to Sunday 6.",
    )
    month_day: int = Field(
        default=-1,
        ge=-1,
        le=31,
        description="For ``monthly``: the day of the month, 1 to 31 (the last day of a "
        "shorter month for 29 to 31), or -1 for the last day of every month.",
    )
    dates: list[date] = Field(
        default_factory=list,
        max_length=62,
        description="For ``dates``: the days it runs on, each in the refresh's time zone.",
    )
    times: dict[int, str] = Field(
        default_factory=dict,
        description="A time, HH:MM, for some days of the week (Monday 0 to Sunday 6) in "
        "place of the usual one.",
    )

    @model_validator(mode="after")
    def _known_days_and_times(self) -> Self:
        """Refuse a day of the week or a time that cannot be."""
        for day, value in self.times.items():
            if not 0 <= day <= 6 or not re.match(TIME_PATTERN, value):
                raise ValueError(f"Day {day} at {value!r} is not a day of the week and a time.")
        if self.month_day == 0:
            raise ValueError("The day of the month is 1 to 31, or -1 for the last day.")
        return self
