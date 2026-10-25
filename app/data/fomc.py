"""FOMC meeting dates from the Federal Reserve's published calendar.

The Fed lists each year's scheduled meetings at :data:`FOMC_CALENDAR_URL`; it is public
and needs no key, so every account gets the dates. The page is read and kept for a day,
in memory and in Redis. When it cannot be fetched or read, :data:`FALLBACK_MEETINGS`,
copied from it in October 2026, stands in.
"""

import json
import logging
import re
import time
from datetime import date

import httpx2
from bs4 import BeautifulSoup

from app.core.cache import cache_get, cache_set
from app.models.markets import FomcMeeting

logger = logging.getLogger(__name__)

FOMC_CALENDAR_URL: str = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_TTL_SECONDS: float = 24 * 60 * 60
"""How long the meeting dates are reused."""
HTTP_TIMEOUT_SECONDS: float = 10.0
CACHE_KEY: str = "fomc:meetings"

MONTHS: dict[str, int] = {
    name: number
    for number, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}


def _meeting(year: int, months: list[int], days: list[int], projections: bool) -> FomcMeeting:
    return FomcMeeting(
        start=date(year, months[0], days[0]),
        end=date(year, months[-1], days[-1]),
        projections=projections,
    )


FALLBACK_MEETINGS: list[FomcMeeting] = [
    _meeting(year, [month], [first, last], projections)
    for year, month, first, last, projections in [
        (2026, 1, 27, 28, False),
        (2026, 3, 17, 18, True),
        (2026, 4, 28, 29, False),
        (2026, 6, 16, 17, True),
        (2026, 7, 28, 29, False),
        (2026, 9, 15, 16, True),
        (2026, 10, 27, 28, False),
        (2026, 12, 8, 9, True),
        (2027, 1, 26, 27, False),
        (2027, 3, 16, 17, True),
        (2027, 4, 27, 28, False),
        (2027, 6, 8, 9, True),
        (2027, 7, 27, 28, False),
        (2027, 9, 14, 15, True),
        (2027, 10, 26, 27, False),
        (2027, 12, 7, 8, True),
    ]
]
"""The 2026 and 2027 schedules as the Fed published them, for when its page cannot be
read."""

_memory: tuple[float, list[FomcMeeting]] | None = None


def clear_fomc_memory() -> None:
    """Forget the meeting dates kept in memory."""
    global _memory
    _memory = None


def parse_meetings(html: str) -> list[FomcMeeting]:
    """Read the scheduled meetings from the Fed's calendar page, in date order.

    Each year's panel lists meetings by month, such as ``March`` with ``17-18*``, where an
    asterisk marks a Summary of Economic Projections; a meeting spanning two months reads
    ``Apr/May`` with ``30-1``. Unscheduled meetings and notation votes are skipped.
    """
    soup = BeautifulSoup(html, "html.parser")
    meetings: list[FomcMeeting] = []
    for panel in soup.select("div.panel"):
        heading = panel.find(["h4", "h5"])
        found = re.search(r"(\d{4}) FOMC Meetings", heading.get_text(" ") if heading else "")
        if not found:
            continue
        year = int(found.group(1))
        for row in panel.select("div.fomc-meeting"):
            month_cell = row.select_one(".fomc-meeting__month")
            date_cell = row.select_one(".fomc-meeting__date")
            if month_cell is None or date_cell is None:
                continue
            day_text = date_cell.get_text(" ", strip=True)
            if "unscheduled" in day_text.lower() or "notation" in day_text.lower():
                continue
            months = [
                MONTHS[name]
                for name in re.findall(r"[a-z]+", month_cell.get_text(" ", strip=True).lower())
                if name in MONTHS
            ]
            days = [int(day) for day in re.findall(r"\d{1,2}", day_text)]
            if not months or not 1 <= len(days) <= 2:
                continue
            try:
                meetings.append(_meeting(year, months, days, "*" in day_text))
            except ValueError:
                logger.warning("Skipping an unreadable FOMC meeting: %s %s", months, day_text)
    return sorted(meetings, key=lambda meeting: meeting.start)


async def fetch_fomc_meetings(client: httpx2.AsyncClient | None = None) -> list[FomcMeeting]:
    """Every FOMC meeting the Fed's calendar lists, in date order.

    Never raises: when the calendar cannot be fetched or read, the meetings kept from
    earlier, or :data:`FALLBACK_MEETINGS`, are returned.
    """
    global _memory
    if _memory is not None and time.monotonic() - _memory[0] < FOMC_TTL_SECONDS:
        return _memory[1]
    cached = await cache_get(CACHE_KEY)
    if cached is not None:
        try:
            meetings = [FomcMeeting.model_validate(item) for item in json.loads(cached)]
            _memory = (time.monotonic(), meetings)
            return meetings
        except ValueError:
            logger.warning("Ignoring unreadable cached FOMC meetings.")
    try:
        headers = {"User-Agent": "PyQxel market calendar"}
        if client is not None:
            response = await client.get(FOMC_CALENDAR_URL, headers=headers)
        else:
            async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as own_client:
                response = await own_client.get(FOMC_CALENDAR_URL, headers=headers)
        response.raise_for_status()
        meetings = parse_meetings(response.text)
    except httpx2.HTTPError as exc:
        logger.warning("Could not fetch the FOMC calendar: %s", exc)
        meetings = []
    if not meetings:
        return FALLBACK_MEETINGS
    _memory = (time.monotonic(), meetings)
    await cache_set(
        CACHE_KEY,
        json.dumps([meeting.model_dump(mode="json") for meeting in meetings]),
        FOMC_TTL_SECONDS,
    )
    return meetings
