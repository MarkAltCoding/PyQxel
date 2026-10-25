"""Markets routes: market-wide news, and a calendar of FOMC meetings, major companies'
earnings reports, IPOs and market holidays.

FOMC dates come from the Federal Reserve and need no key; the rest comes from Finnhub on
the user's own key. :func:`market_context` gathers the same data for AI analyses.
"""

import asyncio
import logging
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.endpoints.stocks import upstream_error
from app.data.fetcher import (
    DataFetchError,
    FinnhubRateLimitError,
    ProviderNotConfiguredError,
    ProviderPlanError,
    finnhub_key,
)
from app.data.fomc import fetch_fomc_meetings
from app.data.markets import (
    fetch_earnings_calendar,
    fetch_ipo_calendar,
    fetch_market_holidays,
    fetch_market_news,
    fetch_market_status,
)
from app.db.screener import market_caps
from app.db.session import get_sessionmaker
from app.models.markets import (
    EarningsEvent,
    MarketCalendar,
    MarketContext,
    MarketNews,
    MarketNewsCategory,
)
from app.stats.markets import (
    CONTEXT_EVENT_DAYS,
    MAJOR_MARKET_CAP,
    major_earnings,
    market_context,
)

logger = logging.getLogger(__name__)

router = APIRouter()

PART_NAMES: tuple[str, ...] = ("earnings reports", "IPOs", "market holidays", "market status")


async def _major(events: list[EarningsEvent], min_market_cap: float) -> list[EarningsEvent]:
    """The major companies' reports among ``events``, sized from the screened universe."""
    try:
        async with get_sessionmaker()() as session:
            caps = await market_caps(session, {event.symbol for event in events})
    except (SQLAlchemyError, OSError) as exc:
        logger.error("Could not read market caps for the earnings calendar: %s", exc)
        caps = {}
    return major_earnings(events, caps, min_market_cap)


def _missing(name: str, error: BaseException) -> str:
    if isinstance(error, ProviderPlanError):
        return f"your Finnhub plan does not cover {name}"
    if isinstance(error, FinnhubRateLimitError):
        return f"{name} hit Finnhub's rate limit"
    return f"{name} could not be fetched"


async def _finnhub_parts(
    start: date, end: date, min_market_cap: float = MAJOR_MARKET_CAP
) -> tuple[list[Any], list[str]]:
    """Earnings, IPOs, holidays and the market status, each ``None`` when Finnhub could
    not supply it, and why."""
    if finnhub_key() is None:
        return [None] * len(PART_NAMES), []
    results = await asyncio.gather(
        fetch_earnings_calendar(start, end),
        fetch_ipo_calendar(start, end),
        fetch_market_holidays(),
        fetch_market_status(),
        return_exceptions=True,
    )
    parts: list[Any] = []
    missing: list[str] = []
    for name, result in zip(PART_NAMES, results):
        if isinstance(result, ProviderNotConfiguredError):
            raise result
        if isinstance(result, DataFetchError):
            missing.append(_missing(name, result))
            parts.append(None)
        elif isinstance(result, BaseException):
            raise result
        else:
            parts.append(result)
    if parts[0] is not None:
        parts[0] = await _major(parts[0], min_market_cap)
    return parts, missing


@router.get("/calendar", response_model=MarketCalendar, summary="Market calendar")
async def get_calendar(
    days: Annotated[int, Query(ge=1, le=60, description="Days ahead, today included.")] = 30,
    min_market_cap: Annotated[
        float,
        Query(ge=1e9, description="Smallest market cap, in US dollars, of companies listed."),
    ] = MAJOR_MARKET_CAP,
) -> MarketCalendar:
    """Return the market-moving dates of the next ``days`` days.

    ``fomc`` lists the Federal Reserve's scheduled FOMC meetings, from its own calendar,
    for every account. With a Finnhub key, ``earnings`` lists major companies' earnings
    reports (a market cap of $10 billion or more, or for companies outside the screened
    universe a quarterly revenue estimate of $2 billion or more), ``ipos`` the IPO
    calendar, ``holidays`` US market holidays and early closes, and ``status`` whether
    the market is open. ``min_market_cap`` sets which companies' reports are listed
    ($10 billion by default). Calendars are reused for six hours. A part that could not be
    supplied is left empty and ``notice`` says why.
    """
    start = date.today()
    end = start + timedelta(days=days - 1)
    meetings, (parts, missing) = await asyncio.gather(
        fetch_fomc_meetings(), _finnhub_parts(start, end, min_market_cap)
    )
    earnings, ipos, holidays, market_status = parts
    notices = []
    if finnhub_key() is None:
        notices.append(
            "Earnings reports, IPOs and market holidays come from Finnhub: add a key in "
            "Settings to see them."
        )
    if missing:
        notices.append(f"Left out: {'; '.join(missing)}.")
    return MarketCalendar(
        start=start,
        end=end,
        fomc=[meeting for meeting in meetings if meeting.end >= start and meeting.start <= end],
        earnings=earnings or [],
        ipos=ipos or [],
        holidays=[holiday for holiday in holidays or [] if start <= holiday.date <= end],
        status=market_status,
        notice=" ".join(notices) or None,
    )


@router.get("/news", response_model=MarketNews, summary="Market news")
async def get_market_news(
    category: Annotated[MarketNewsCategory, Query(description="News category.")] = "general",
) -> MarketNews:
    """Return the latest market-wide news in ``category``, newest first, from Finnhub.

    ``general`` is business and markets news, ``forex`` currencies, ``crypto``
    cryptocurrencies, and ``merger`` mergers and acquisitions. Reused for five minutes.
    Returns 403 without a valid Finnhub key, and 502 when Finnhub fails.
    """
    try:
        return await fetch_market_news(category)
    except DataFetchError as exc:
        raise upstream_error(exc) from exc


async def _general_news() -> MarketNews | None:
    """The general market news, or ``None`` without a Finnhub key or when Finnhub fails."""
    if finnhub_key() is None:
        return None
    try:
        return await fetch_market_news("general")
    except ProviderNotConfiguredError:
        raise
    except DataFetchError as exc:
        logger.info("Market news was left out of an analysis: %s", exc)
        return None


async def fetch_market_context(today: date) -> MarketContext:
    """The market backdrop for an analysis: upcoming FOMC meetings, holidays, major
    earnings reports and IPOs, and the newest market headlines.

    FOMC meetings come for every account; the rest needs a Finnhub key and is left empty
    without one, or when Finnhub cannot supply it.

    Raises:
        ProviderNotConfiguredError: If Finnhub rejects the user's key.
    """
    end = today + timedelta(days=CONTEXT_EVENT_DAYS)
    meetings, (parts, _), news = await asyncio.gather(
        fetch_fomc_meetings(), _finnhub_parts(today, end), _general_news()
    )
    earnings, ipos, holidays, market_status = parts
    return market_context(today, meetings, holidays, earnings, ipos, news, market_status)
