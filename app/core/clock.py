"""Today's date and a moment's day as the US markets count them, in New York time.

The server runs in UTC, so from 7 or 8 p.m. in New York (by daylight saving) until
midnight UTC "today" there is already tomorrow. Every date PyQxel shows or reasons from,
such as the day a news story ran, the end of a window of prices, or the day a list of
picks was made, is the New York date, the one the exchanges and a US investor use.
Moments themselves are still stored in UTC.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

MARKET_ZONE: ZoneInfo = ZoneInfo("America/New_York")
"""The US exchanges' time zone, Eastern Time."""


def market_now() -> datetime:
    """The current moment in New York time."""
    return datetime.now(MARKET_ZONE)


def market_today() -> date:
    """Today's date in New York."""
    return market_now().date()


def market_day(moment: datetime) -> date:
    """The New York date of ``moment``; a naive moment is taken as UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(MARKET_ZONE).date()
