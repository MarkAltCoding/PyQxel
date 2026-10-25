"""Choosing the market-moving events of a calendar, and the market backdrop given to Claude."""

import re
from datetime import date, timedelta

from app.models.markets import (
    EarningsEvent,
    FomcMeeting,
    IpoEvent,
    MarketContext,
    MarketHoliday,
    MarketNews,
    MarketStatus,
)
from app.models.news import NewsHeadline

MAJOR_MARKET_CAP: float = 10e9
"""Market cap, in US dollars, from which a company's earnings report is listed by default."""
REVENUE_PER_MARKET_CAP: float = 0.2
"""Quarterly revenue estimate, per dollar of the market cap threshold, from which a company
outside the screened universe is listed: $2 billion a quarter for $10 billion, cautious
enough that small companies with large sales rarely pass."""
CONTEXT_FOMC_MEETINGS: int = 2
"""Upcoming FOMC meetings given to Claude."""
CONTEXT_HOLIDAY_DAYS: int = 30
"""Days ahead holidays are given to Claude."""
CONTEXT_EVENT_DAYS: int = 14
"""Days ahead earnings reports and IPOs are given to Claude."""
CONTEXT_EARNINGS: int = 25
"""Major earnings reports given to Claude, the largest companies'."""
CONTEXT_IPOS: int = 10
"""IPOs given to Claude, the largest."""
CONTEXT_HEADLINES: int = 15
"""Market headlines given to Claude."""
SUMMARY_CHARS: int = 240
"""Length each headline's summary given to Claude is cut to."""


_STATE_SUFFIX = re.compile(r"\s*/[A-Za-z]{2,3}/?\s*$|/\s*$")


def _name(name: str) -> str:
    """A company name as SEC listings spell it, read well: in title case when in capitals,
    and without the state of incorporation some carry, as in ``Amphenol Corp /DE/``."""
    name = _STATE_SUFFIX.sub("", name).strip()
    return name.title() if name.isupper() else name


def major_earnings(
    events: list[EarningsEvent],
    caps: dict[str, tuple[str, float]],
    min_market_cap: float = MAJOR_MARKET_CAP,
) -> list[EarningsEvent]:
    """The reports of companies worth ``min_market_cap`` or more, named and with their
    market caps where known.

    A company outside the screened universe, such as a foreign issuer, has no known market
    cap, so it is listed from :data:`REVENUE_PER_MARKET_CAP` times ``min_market_cap`` of
    quarterly revenue estimate.
    """
    major: list[EarningsEvent] = []
    for event in events:
        known = caps.get(event.symbol)
        if known is not None:
            name, cap = known
            if cap >= min_market_cap:
                major.append(event.model_copy(update={"name": _name(name), "market_cap": cap}))
        elif (event.revenue_estimate or 0) >= min_market_cap * REVENUE_PER_MARKET_CAP:
            major.append(event)
    return major


def _size(event: EarningsEvent) -> float:
    """How large the company is, for ranking: its market cap, or else its revenue estimate
    at about five times sales, which ranks it among companies of known size."""
    if event.market_cap is not None:
        return event.market_cap
    return (event.revenue_estimate or 0) * 4 * 5


def _shorten(text: str | None) -> str | None:
    if text is None or len(text) <= SUMMARY_CHARS:
        return text
    return text[:SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def market_context(
    today: date,
    fomc: list[FomcMeeting],
    holidays: list[MarketHoliday] | None,
    earnings: list[EarningsEvent] | None,
    ipos: list[IpoEvent] | None,
    news: MarketNews | None,
    status: MarketStatus | None,
) -> MarketContext:
    """The upcoming FOMC meetings, holidays, major earnings reports and IPOs, and the
    newest market headlines; parts passed as ``None`` are left empty."""
    soon = today + timedelta(days=CONTEXT_EVENT_DAYS)
    return MarketContext(
        as_of=today,
        market_open=None if status is None else status.is_open,
        fomc_meetings=[meeting for meeting in fomc if meeting.end >= today][:CONTEXT_FOMC_MEETINGS],
        holidays=[
            holiday
            for holiday in holidays or []
            if today <= holiday.date <= today + timedelta(days=CONTEXT_HOLIDAY_DAYS)
        ],
        major_earnings=sorted(
            (event for event in earnings or [] if today <= event.date <= soon),
            key=_size,
            reverse=True,
        )[:CONTEXT_EARNINGS],
        ipos=sorted(
            (ipo for ipo in ipos or [] if today <= ipo.date <= soon and ipo.status != "withdrawn"),
            key=lambda ipo: ipo.value or 0,
            reverse=True,
        )[:CONTEXT_IPOS],
        headlines=[
            NewsHeadline(
                published_at=article.published_at,
                source=article.source,
                headline=article.headline,
                summary=_shorten(article.summary),
            )
            for article in (news.articles if news else [])[:CONTEXT_HEADLINES]
        ],
    )
