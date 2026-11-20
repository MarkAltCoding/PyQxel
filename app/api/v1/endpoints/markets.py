"""Markets routes: market-wide news, and a calendar of FOMC meetings, major companies'
earnings reports, IPOs, and market holidays.

FOMC dates come from the Federal Reserve and need no key; the rest comes from Finnhub on
the user's own key. :func:`market_context` gathers the same data for AI analyses.
"""

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
from app.data.economy import fetch_bls, fetch_yield_curve
from app.data.markets import (
    EARNINGS_CHUNK_DAYS,
    fetch_earnings_calendar,
    fetch_ipo_calendar,
    fetch_market_holidays,
    fetch_any_market_news,
    fetch_market_status,
)
from app.db.screener import market_caps, shows_universe
from app.db.session import get_sessionmaker
from app.models.markets import (
    EconomyContext,
    EarningsEvent,
    MarketCalendar,
    MarketContext,
    MarketNews,
    MarketNewsCategory,
)
from app.models.news import NewsHeadline
from app.stats.economy import economy_context
from app.stats.markets import (
    MAJOR_MARKET_CAP,
    major_earnings,
    market_context,
    score_headlines,
    top_headlines,
)

logger = logging.getLogger(__name__)

router = APIRouter()

PART_NAMES: tuple[str, ...] = ("earnings reports", "IPOs", "market holidays", "market status")


async def _major(
    events: list[EarningsEvent], min_market_cap: float, max_market_cap: float | None = None
) -> list[EarningsEvent]:
    """The major companies' reports among ``events``, sized from the screened universe."""
    try:
        async with get_sessionmaker()() as session:
            caps = (
                await market_caps(session, {event.symbol for event in events})
                if await shows_universe(session)
                else {}
            )
    except (SQLAlchemyError, OSError) as exc:
        logger.error("Could not read market caps for the earnings calendar: %s", exc)
        caps = {}
    return major_earnings(events, caps, min_market_cap, max_market_cap)


def _missing(name: str, error: BaseException) -> str:
    if isinstance(error, ProviderPlanError):
        return f"your Finnhub plan does not cover {name}"
    if isinstance(error, FinnhubRateLimitError):
        return f"{name} hit Finnhub's rate limit"
    return f"{name} could not be fetched"


async def _finnhub_parts(
    start: date,
    end: date,
    min_market_cap: float = MAJOR_MARKET_CAP,
    max_market_cap: float | None = None,
) -> tuple[list[Any], list[str]]:
    """Earnings, IPOs, holidays, and the market status, each ``None`` when Finnhub could
    not supply it, and why. Only major companies' earnings reports are kept."""
    parts, missing = await _finnhub_raw_parts(start, end)
    if parts[0] is not None:
        parts[0] = await _major(parts[0], min_market_cap, max_market_cap)
    return parts, missing


async def _finnhub_raw_parts(start: date, end: date) -> tuple[list[Any], list[str]]:
    """As :func:`_finnhub_parts`, with every company's earnings reports."""
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
    return parts, missing


MAX_CALENDAR_DAYS: int = 90
"""Longest calendar window, in days; a quarter covers a full earnings season."""

MAX_MIN_MARKET_CAP: float = 5e12
"""Largest market cap threshold, in US dollars, above every listed company."""


@router.get("/calendar", response_model=MarketCalendar, summary="Market calendar")
async def get_calendar(
    days: Annotated[
        int, Query(ge=1, le=MAX_CALENDAR_DAYS, description="Days ahead, today included.")
    ] = 30,
    min_market_cap: Annotated[
        float,
        Query(
            ge=0,
            le=MAX_MIN_MARKET_CAP,
            description="Smallest market cap, in US dollars, of companies listed.",
        ),
    ] = MAJOR_MARKET_CAP,
    max_market_cap: Annotated[
        float | None,
        Query(
            ge=1e9,
            le=MAX_MIN_MARKET_CAP,
            description="If given, only companies worth less than this, in US dollars.",
        ),
    ] = None,
) -> MarketCalendar:
    """Return the market-moving dates of the next ``days`` days.

    ``fomc`` lists the Federal Reserve's scheduled FOMC meetings, from its own calendar,
    for every account. With a Finnhub key, ``earnings`` lists major companies' earnings
    reports (a market cap of $10 billion or more, or for companies outside the screened
    universe a quarterly revenue estimate of $2 billion or more), ``ipos`` the IPO
    calendar, ``holidays`` US market holidays and early closes, and ``status`` whether
    the market is open. ``min_market_cap`` sets which companies' reports are listed
    ($10 billion by default), and ``max_market_cap``, if given, lists only those worth
    less (send ``min_market_cap=0`` for every company below it). Calendars are reused for
    six hours. A part that could not be supplied is left empty and ``notice`` says why.
    """
    start = date.today()
    end = start + timedelta(days=days - 1)
    meetings, (parts, missing) = await asyncio.gather(
        fetch_fomc_meetings(), _finnhub_parts(start, end, min_market_cap, max_market_cap)
    )
    earnings, ipos, holidays, market_status = parts
    notices = []
    if finnhub_key() is None:
        notices.append(
            "Earnings reports, IPOs, and market holidays come from Finnhub: add a key in "
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
    """Return the latest market-wide news in ``category``, newest first, from Finnhub, or
    from Alpha Vantage's news feed (a paid plan) when Finnhub cannot supply it.

    ``general`` is business and markets news, ``forex`` currencies, ``crypto``
    cryptocurrencies, and ``merger`` mergers and acquisitions. Each article's
    ``importance``, from 0 to 1, scores how market-moving it looks: the topics its
    headline names, its publisher, and its age. Reused for five minutes (Alpha Vantage's
    feed for fifteen). Returns 403 when neither key can supply it, and 502 when Finnhub
    fails and Alpha Vantage cannot stand in.
    """
    try:
        news = await fetch_any_market_news(category)
    except DataFetchError as exc:
        raise upstream_error(exc) from exc
    return score_headlines(news, datetime.now(timezone.utc))


async def _general_news(category: MarketNewsCategory = "general") -> MarketNews | None:
    """The market news in ``category``, from Finnhub or Alpha Vantage's feed, or ``None``
    when neither can supply it."""
    try:
        return await fetch_any_market_news(category)
    except ProviderNotConfiguredError:
        if finnhub_key() is not None:
            raise
        return None
    except DataFetchError as exc:
        logger.info("Market news (%s) was left out of an analysis: %s", category, exc)
        return None


async def fetch_economy(today: date) -> EconomyContext | None:
    """The yield curve, inflation, and jobs, from the Treasury and the Bureau of Labor
    Statistics (public, no key); a source that cannot be reached is left out."""
    curve, figures = await asyncio.gather(
        fetch_yield_curve(today), fetch_bls(today), return_exceptions=True
    )
    for name, result in (("yield curve", curve), ("BLS figures", figures)):
        if isinstance(result, DataFetchError):
            logger.info("The %s was left out of the backdrop: %s", name, result)
        elif isinstance(result, BaseException):
            raise result
    return economy_context(
        None if isinstance(curve, BaseException) else curve,
        None if isinstance(figures, BaseException) else figures,
        today,
    )


REPORT_CALENDAR_DAYS: int = 90
"""Days of calendar a stock report is given: a quarter, in which every company reports."""

REPORT_FOMC_DAYS: int = 365
"""Days ahead a stock report lists FOMC meetings."""

REPORT_EARNINGS: int = 50
"""Major earnings reports a stock report lists, the largest companies'."""

REPORT_IPOS: int = 15
"""IPOs a stock report lists, the largest."""


async def fetch_market_context(today: date) -> MarketContext:
    """The market backdrop for an analysis: a year of FOMC meetings, a quarter's holidays,
    major earnings reports, and IPOs, and the most market-moving market headlines.

    FOMC meetings come for every account; the rest needs a Finnhub key and is left empty
    without one, or when Finnhub cannot supply it. The quarter's weekly earnings calendars
    are the Markets page's, cached for six hours.

    Raises:
        ProviderNotConfiguredError: If Finnhub rejects the user's key.
    """
    end = today + timedelta(days=REPORT_CALENDAR_DAYS - 1)
    meetings, (parts, _), news, economy = await asyncio.gather(
        fetch_fomc_meetings(), _finnhub_parts(today, end), _general_news(), fetch_economy(today)
    )
    earnings, ipos, holidays, market_status = parts
    return market_context(
        today,
        meetings,
        holidays,
        earnings,
        ipos,
        news,
        market_status,
        event_days=REPORT_CALENDAR_DAYS,
        holiday_days=REPORT_CALENDAR_DAYS,
        fomc_days=REPORT_FOMC_DAYS,
        earnings_count=REPORT_EARNINGS,
        ipo_count=REPORT_IPOS,
    ).model_copy(update={"calendar_days": REPORT_CALENDAR_DAYS, "economy": economy})


PICKS_EARNINGS_DAYS: int = 28
"""Fewest days of every company's earnings reports gathered for Claude's picks and
portfolio reviews: four of Finnhub's weekly calendar requests, the same ones the Markets
page's calendar makes."""

BACKDROP_EARNINGS: int = 100
"""Major earnings reports listed for picks and reviews, the largest companies', each with
its day, time, and estimates, as the Markets page's calendar lists them."""

BACKDROP_IPOS: int = 25
"""IPOs listed for picks and reviews, the largest."""

FOMC_MAX_DAYS: int = 2 * 365
"""Furthest ahead FOMC meetings are listed; the Federal Reserve publishes about a year and
a half of its schedule."""

DEFAULT_REVIEW_HORIZON_DAYS: float = 365.0
"""The horizon a portfolio review's calendar is set by when the person gave none."""


def backdrop_days(horizon_days: float | None) -> int:
    """Days of calendar for a holding period of ``horizon_days``: the period itself, from
    :data:`PICKS_EARNINGS_DAYS` to the Markets page's longest, :data:`MAX_CALENDAR_DAYS`,
    a quarter, in which every company reports once; ``None`` is the longest."""
    if horizon_days is None:
        return MAX_CALENDAR_DAYS
    return int(min(max(math.ceil(horizon_days), PICKS_EARNINGS_DAYS), MAX_CALENDAR_DAYS))


def backdrop_finnhub_requests(days: int) -> int:
    """Finnhub requests the backdrop of ``days`` sends, before its caches: a weekly
    earnings calendar for each week, the IPOs, holidays, market status, and the news in
    each of the Markets page's four categories."""
    return math.ceil(days / EARNINGS_CHUNK_DAYS) + 7


PICKS_HEADLINES: int = 80
"""General market headlines given to picks and reviews, the most market-moving."""

PICKS_DEAL_HEADLINES: int = 30
"""Headlines on mergers and acquisitions given to picks and reviews."""

PICKS_FOREX_HEADLINES: int = 20
"""Headlines on currencies given to picks and reviews, for holdings abroad and the dollar."""

PICKS_CRYPTO_HEADLINES: int = 10
"""Headlines on cryptocurrencies given to picks and reviews, for crypto funds and miners."""


@dataclass(frozen=True)
class PicksBackdrop:
    """The market backdrop of Claude's picks, with what only the picks use."""

    market: MarketContext
    earnings: list[EarningsEvent] | None
    """Every company's earnings reports over :data:`PICKS_EARNINGS_DAYS` days, to find
    each candidate's next one; ``None`` when the calendar could not be fetched."""
    deals: list[NewsHeadline]
    """The headlines on mergers and acquisitions that look most market-moving."""
    forex: list[NewsHeadline]
    """The same of the currency news."""
    crypto: list[NewsHeadline]
    """The same of the cryptocurrency news."""


async def fetch_picks_backdrop(
    today: date, horizon_days: float | None = None, calendar_days: int | None = None
) -> PicksBackdrop:
    """The market backdrop of :func:`fetch_market_context` for a holding period of
    ``horizon_days``, with more headlines, the news on deals, every FOMC meeting scheduled
    within the period, and the earnings reports, IPOs, and holidays of a calendar as long
    as the period (:func:`backdrop_days`, four weeks to a quarter), all in one round of
    requests: :func:`backdrop_finnhub_requests` to Finnhub, shared with the Markets page
    and cached, and the Federal Reserve's calendar.

    ``calendar_days``, when given, shortens the calendar to leave the Finnhub key's minute
    for company news (:func:`app.api.v1.endpoints.picks.plan_requests`); FOMC meetings
    still cover the whole period.

    Raises:
        ProviderNotConfiguredError: If Finnhub rejects the user's key.
    """
    days = backdrop_days(horizon_days)
    if calendar_days is not None:
        days = max(1, min(days, calendar_days))
    fomc_days = int(min(horizon_days if horizon_days is not None else days, FOMC_MAX_DAYS))
    end = today + timedelta(days=days - 1)
    (meetings, (parts, _), news, deals, forex, crypto), economy = await asyncio.gather(
        asyncio.gather(
            fetch_fomc_meetings(),
            _finnhub_raw_parts(today, end),
            _general_news(),
            _general_news("merger"),
            _general_news("forex"),
            _general_news("crypto"),
        ),
        fetch_economy(today),
    )
    earnings, ipos, holidays, market_status = parts
    major = None if earnings is None else await _major(earnings, MAJOR_MARKET_CAP)
    return PicksBackdrop(
        market=market_context(
            today,
            meetings,
            holidays,
            major,
            ipos,
            news,
            market_status,
            PICKS_HEADLINES,
            event_days=days,
            holiday_days=days,
            fomc_days=max(fomc_days, days),
            earnings_count=BACKDROP_EARNINGS,
            ipo_count=BACKDROP_IPOS,
        ).model_copy(update={"calendar_days": days, "economy": economy}),
        earnings=earnings,
        deals=top_headlines(deals, PICKS_DEAL_HEADLINES),
        forex=top_headlines(forex, PICKS_FOREX_HEADLINES),
        crypto=top_headlines(crypto, PICKS_CRYPTO_HEADLINES),
    )
