"""Choosing the market-moving events of a calendar, and the market backdrop given to Claude."""

import math
import re
from datetime import date, datetime, timedelta

from app.models.markets import (
    EarningsEvent,
    FomcMeeting,
    IpoEvent,
    MarketContext,
    MarketHoliday,
    MarketNews,
    MarketStatus,
)
from app.models.news import NewsArticle, NewsHeadline

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


MAJOR_PUBLISHERS: frozenset[str] = frozenset(
    {
        "reuters",
        "bloomberg",
        "the wall street journal",
        "wsj",
        "dow jones",
        "financial times",
        "ft",
        "associated press",
        "ap",
        "cnbc",
        "marketwatch",
        "barron's",
        "the economist",
        "new york times",
    }
)
"""Publishers whose market headlines weigh more, lower case."""

HEADLINE_TOPICS: tuple[tuple[re.Pattern[str], float], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), weight)
    for pattern, weight in (
        # Monetary policy and the economy move every market at once.
        (r"\b(fed|fomc|federal reserve|powell|central bank|ecb|boj|bank of england)\b", 1.5),
        (r"\b(rate (cut|hike)s?|interest rates?|yields?|treasur(y|ies)|bond market)\b", 1.2),
        (r"\b(inflation|cpi|ppi|pce|jobs report|payrolls?|unemployment|gdp|recession)\b", 1.2),
        (r"\b(tariffs?|trade war|sanctions?|shutdown|debt ceiling|stimulus)\b", 1.0),
        (r"\b(oil|opec|crude|gold|dollar)\b", 0.6),
        # Broad market moves.
        (r"\b(s&p 500|dow|nasdaq|wall street|stocks?|markets?)\b", 0.6),
        (r"\b(sell-?off|rally|record|plunge|crash|surge|tumble|soar|slump|rout)\w*", 0.8),
        # Large corporate events.
        (r"\b(earnings|guidance|forecast|profit warning|revenue)\b", 0.6),
        (r"\b(merger|acquisition|acquires?|buyout|takeover|ipo|bankruptcy|layoffs?)\b", 0.8),
        (r"\b(antitrust|sec|lawsuit|probe|regulators?)\b", 0.5),
        (
            r"\b(apple|microsoft|nvidia|amazon|alphabet|google|meta|tesla|berkshire|jpmorgan)"
            r"\b",
            0.5,
        ),
        (r"\$\s?\d[\d,.]*\s?(billion|bn|trillion|tn)\b", 0.6),
    )
)
"""Topics that make a market headline matter more, with how much each adds."""

TOPIC_SATURATION: float = 3.0
"""Topic weight at which a headline counts as fully on topic."""

IMPORTANCE_HALF_LIFE_HOURS: float = 24.0
"""Hours after which a headline's recency counts half."""


def headline_importance(article: NewsArticle, now: datetime) -> float:
    """Score how market-moving ``article`` looks, from 0 to 1.

    A heuristic, not a model: the topics its headline names (monetary policy and the
    economy most, then market-wide moves and large corporate events), whether a major
    publisher ran it, and how recent it is, halving every
    :data:`IMPORTANCE_HALF_LIFE_HOURS` hours.
    """
    topics = sum(weight for pattern, weight in HEADLINE_TOPICS if pattern.search(article.headline))
    relevance = min(topics / TOPIC_SATURATION, 1.0)
    publisher = 1.0 if article.source.strip().lower() in MAJOR_PUBLISHERS else 0.4
    hours = max((now - article.published_at).total_seconds() / 3600.0, 0.0)
    recency = math.pow(0.5, hours / IMPORTANCE_HALF_LIFE_HOURS)
    return round((0.6 * relevance + 0.4 * publisher) * (0.4 + 0.6 * recency), 4)


def score_headlines(news: MarketNews, now: datetime) -> MarketNews:
    """``news`` with every article's :func:`headline_importance`, still newest first."""
    return news.model_copy(
        update={
            "articles": [
                article.model_copy(update={"importance": headline_importance(article, now)})
                for article in news.articles
            ]
        }
    )
