"""Market-wide news and calendars from Finnhub, on the user's own key.

Finnhub's free plan covers, as checked against a free key in October 2026: the latest
market news by category, the earnings calendar of every US company, the IPO calendar,
US market holidays and whether the market is open. Answers are kept in memory and in
Redis under the provider's name, so they are only served to users who query Finnhub
themselves.

The earnings calendar answers at most :data:`EARNINGS_ROW_LIMIT` rows a request, which a
week of earnings season exceeds, so it is fetched a week at a time and a week that hits
the limit is split until no part does.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx2

from app.data.finnhub import cached_get
from app.data.news import parse_articles
from app.models.markets import (
    EarningsEvent,
    IpoEvent,
    MarketHoliday,
    MarketNews,
    MarketNewsCategory,
    MarketStatus,
)

MARKET_NEWS_TTL_SECONDS: float = 5 * 60
"""How long market news is reused."""
CALENDAR_TTL_SECONDS: float = 6 * 60 * 60
"""How long earnings and IPO calendars are reused."""
HOLIDAY_TTL_SECONDS: float = 24 * 60 * 60
"""How long the holiday list is reused."""
STATUS_TTL_SECONDS: float = 60
"""How long the market status is reused."""
MARKET_NEWS_DAYS: int = 7
"""Market news older than this is left out."""
EARNINGS_ROW_LIMIT: int = 1500
"""Rows Finnhub's earnings calendar answers at most; a request that hits it is split."""
EARNINGS_CHUNK_DAYS: int = 7
"""Days of the earnings calendar asked for at once."""


def _day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _text(value: object) -> str | None:
    text = " ".join(str(value).split()) if isinstance(value, (str, int, float)) else ""
    return text or None


def _rows(payload: Any, key: str) -> list[dict[str, Any]]:
    rows = payload.get(key) if isinstance(payload, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


async def fetch_market_news(
    category: MarketNewsCategory, client: httpx2.AsyncClient | None = None
) -> MarketNews:
    """The latest market news in ``category``, newest first, without duplicate headlines."""
    payload = await cached_get("news", {"category": category}, client, ttl=MARKET_NEWS_TTL_SECONDS)
    now = datetime.now(timezone.utc)
    return MarketNews(
        category=category,
        articles=parse_articles(payload, now - timedelta(days=MARKET_NEWS_DAYS)),
        fetched_at=now,
    )


def _earnings(row: dict[str, Any]) -> EarningsEvent | None:
    symbol, day = _text(row.get("symbol")), _day(row.get("date"))
    if symbol is None or day is None:
        return None
    quarter, year = row.get("quarter"), row.get("year")
    return EarningsEvent(
        symbol=symbol.replace(".", "-"),
        date=day,
        hour=_text(row.get("hour")),
        quarter=quarter if isinstance(quarter, int) else None,
        year=year if isinstance(year, int) else None,
        eps_estimate=_number(row.get("epsEstimate")),
        revenue_estimate=_number(row.get("revenueEstimate")),
    )


async def _earnings_rows(
    start: date, end: date, client: httpx2.AsyncClient | None
) -> list[dict[str, Any]]:
    """Every earnings calendar row from ``start`` to ``end``, splitting at the row limit."""
    payload = await cached_get(
        "calendar/earnings",
        {"from": start.isoformat(), "to": end.isoformat()},
        client,
        ttl=CALENDAR_TTL_SECONDS,
    )
    rows = _rows(payload, "earningsCalendar")
    if len(rows) < EARNINGS_ROW_LIMIT or start == end:
        return rows
    middle = start + (end - start) // 2
    first, second = await asyncio.gather(
        _earnings_rows(start, middle, client),
        _earnings_rows(middle + timedelta(days=1), end, client),
    )
    return first + second


async def fetch_earnings_calendar(
    start: date, end: date, client: httpx2.AsyncClient | None = None
) -> list[EarningsEvent]:
    """Every US company's earnings reports scheduled from ``start`` to ``end``, by date."""
    chunks = []
    day = start
    while day <= end:
        last = min(end, day + timedelta(days=EARNINGS_CHUNK_DAYS - 1))
        chunks.append(_earnings_rows(day, last, client))
        day = last + timedelta(days=1)
    events: dict[tuple[str, date], EarningsEvent] = {}
    for rows in await asyncio.gather(*chunks):
        for row in rows:
            event = _earnings(row)
            if event is not None and start <= event.date <= end:
                events[event.symbol, event.date] = event
    return sorted(events.values(), key=lambda event: (event.date, event.symbol))


async def fetch_ipo_calendar(
    start: date, end: date, client: httpx2.AsyncClient | None = None
) -> list[IpoEvent]:
    """IPOs scheduled, priced, filed or withdrawn from ``start`` to ``end``, by date."""
    payload = await cached_get(
        "calendar/ipo",
        {"from": start.isoformat(), "to": end.isoformat()},
        client,
        ttl=CALENDAR_TTL_SECONDS,
    )
    ipos: list[IpoEvent] = []
    for row in _rows(payload, "ipoCalendar"):
        day, name = _day(row.get("date")), _text(row.get("name"))
        if day is None or name is None:
            continue
        ipos.append(
            IpoEvent(
                date=day,
                symbol=_text(row.get("symbol")),
                name=name.title() if name.isupper() else name,
                exchange=_text(row.get("exchange")),
                shares=_number(row.get("numberOfShares")),
                price=_text(row.get("price")),
                status=_text(row.get("status")),
                value=_number(row.get("totalSharesValue")) or None,
            )
        )
    return sorted(ipos, key=lambda ipo: ipo.date)


async def fetch_market_holidays(client: httpx2.AsyncClient | None = None) -> list[MarketHoliday]:
    """US market holidays and early closes Finnhub lists, past and future, by date."""
    payload = await cached_get(
        "stock/market-holiday", {"exchange": "US"}, client, ttl=HOLIDAY_TTL_SECONDS
    )
    holidays: list[MarketHoliday] = []
    for row in _rows(payload, "data"):
        day, name = _day(row.get("atDate")), _text(row.get("eventName"))
        if day is None or name is None:
            continue
        holidays.append(
            MarketHoliday(date=day, name=name, trading_hours=_text(row.get("tradingHour")))
        )
    return sorted(holidays, key=lambda holiday: holiday.date)


async def fetch_market_status(client: httpx2.AsyncClient | None = None) -> MarketStatus:
    """Whether US exchanges are trading now, and in which session."""
    payload = await cached_get(
        "stock/market-status", {"exchange": "US"}, client, ttl=STATUS_TTL_SECONDS
    )
    payload = payload if isinstance(payload, dict) else {}
    stamp = _number(payload.get("t"))
    return MarketStatus(
        is_open=bool(payload.get("isOpen")),
        session=_text(payload.get("session")),
        holiday=_text(payload.get("holiday")),
        as_of=(
            datetime.fromtimestamp(stamp, timezone.utc) if stamp else datetime.now(timezone.utc)
        ),
    )
