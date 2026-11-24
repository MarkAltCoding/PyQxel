"""News from Alpha Vantage, where Finnhub cannot supply it, in as few requests as possible.

Alpha Vantage's ``NEWS_SENTIMENT`` function needs a paid plan: a free key, checked on
November 20, 2026, is told it is "a premium endpoint". One request with no tickers named
returns up to :data:`FEED_LIMIT` of the latest articles across the market, and each one
lists the tickers it mentions with a relevance score. So a single request, kept for
:data:`FEED_TTL_SECONDS` and shared by every page and report, serves the market's news and
every company's: naming tickers instead would cost a request per company, and Alpha
Vantage reads several tickers as articles that mention all of them at once.

A plan that refuses the function is remembered for a day, in Redis as well as memory, so a
free key spends at most one of its 25 daily requests finding out; a key already known to be
on the free plan (:func:`app.data.fetcher.alpha_vantage_free_history`) is not asked at all.
"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.clock import MARKET_ZONE, market_day
from app.core.cache import cache_get, cache_set
from app.core.credentials import current_provider_keys
from app.data import fetcher
from app.data.fetcher import (
    DataFetchError,
    ProviderNotConfiguredError,
    ProviderPlanError,
    alpha_vantage_free_history,
    alpha_vantage_get,
    alpha_vantage_key,
)
from app.models.markets import MarketNews, MarketNewsCategory
from app.models.news import CompanyNews, NewsArticle

logger = logging.getLogger(__name__)

FUNCTION: str = "NEWS_SENTIMENT"
FEED_LIMIT: int = 1000
"""Articles asked for in the one request, the most Alpha Vantage returns."""
FEED_PARAMS: dict[str, str] = {"limit": str(FEED_LIMIT), "sort": "LATEST"}
"""The request's parameters: fixed, so every caller shares one cached answer."""
FEED_TTL_SECONDS: float = 15 * 60
"""How long the feed is reused: at most four requests an hour, however much is viewed."""
REFUSED_SECONDS: float = 24 * 60 * 60
"""How long a plan's refusal is remembered before asking again."""
MIN_RELEVANCE: float = 0.15
"""Least relevance, from 0 to 1, of an article to a ticker for it to count as that
company's news; lower scores are passing mentions."""
MARKET_DAYS: int = 7
"""Market news older than this is left out, as Finnhub's is."""
COMPANY_DAYS: int = 14
"""Days a company's news window spans, as Finnhub's does."""
SUMMARY_MAX_CHARS: int = 600

CATEGORY_TOPICS: dict[MarketNewsCategory, frozenset[str] | None] = {
    "general": None,
    "merger": frozenset({"mergers_and_acquisitions"}),
    "crypto": frozenset({"blockchain"}),
    "forex": None,
}
"""Alpha Vantage topics that make up each market news category; ``None`` keeps every
article (forex has no topic of its own, so it is read from the tickers instead)."""

_refused: dict[str, float] = {}
"""Key digest -> when its plan refused the function, for deployments without Redis."""

_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class FeedArticle:
    """One article of the feed, with the tickers and topics it bears on."""

    article: NewsArticle
    tickers: dict[str, float]
    """Each ticker it mentions, as PyQxel writes it (BRK-B), with its relevance."""
    topics: frozenset[str]


def _refused_key(api_key: str) -> str:
    return f"alphavantage:nonews:{fetcher._key_digest(api_key)}"


async def news_refused(api_key: str) -> bool:
    """Whether ``api_key``'s plan is known not to cover Alpha Vantage's news."""
    if alpha_vantage_free_history(api_key):
        return True
    key = _refused_key(api_key)
    when = _refused.get(key)
    if when is not None and time.monotonic() - when < REFUSED_SECONDS:
        return True
    return await cache_get(key) is not None


async def feed_cached() -> bool:
    """Whether the feed is cached for the request's key, so reading it sends no request."""
    found = current_provider_keys().alpha_vantage
    cache_key = (
        "alphavantage:"
        + ":".join([FUNCTION, *(f"{name}={value}" for name, value in sorted(FEED_PARAMS.items()))])
        + fetcher.key_scope(None if found is None else found.get_secret_value())
    )
    return cache_key in fetcher._alpha_vantage_memory or await cache_get(cache_key) is not None


def _clean(value: object) -> str:
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def _number(value: object) -> float | None:
    try:
        return float(str(value))
    except ValueError:
        return None


def _published(value: object) -> datetime | None:
    """Alpha Vantage's ``20261120T103000`` as a UTC time."""
    try:
        return datetime.strptime(str(value), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _ticker(value: str) -> str:
    """A ticker as PyQxel writes it: a share class with a hyphen (BRK.B -> BRK-B)."""
    return value.strip().upper().replace(".", "-")


def _headline_key(headline: str) -> str:
    return " ".join(_WORD.findall(headline.lower()))


def parse_feed(payload: dict[str, Any]) -> list[FeedArticle]:
    """The feed's articles, newest first, each headline once (its earliest copy)."""
    rows = payload.get("feed")
    if not isinstance(rows, list):
        raise DataFetchError("Alpha Vantage returned news in an unexpected form.")
    earliest: dict[str, FeedArticle] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        headline, url = _clean(row.get("title")), _clean(row.get("url"))
        published = _published(row.get("time_published"))
        if not headline or not url.startswith(("https://", "http://")) or published is None:
            continue
        summary = _clean(row.get("summary"))
        if _headline_key(summary) == _headline_key(headline):
            summary = ""
        if len(summary) > SUMMARY_MAX_CHARS:
            summary = summary[:SUMMARY_MAX_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"
        tickers: dict[str, float] = {}
        for mention in row.get("ticker_sentiment") or []:
            if not isinstance(mention, dict) or not isinstance(mention.get("ticker"), str):
                continue
            relevance = _number(mention.get("relevance_score"))
            if relevance is not None:
                tickers[_ticker(mention["ticker"])] = relevance
        topics = frozenset(
            topic["topic"]
            for topic in row.get("topics") or []
            if isinstance(topic, dict) and isinstance(topic.get("topic"), str)
        )
        found = FeedArticle(
            article=NewsArticle(
                published_at=published,
                headline=headline,
                summary=summary or None,
                source=_clean(row.get("source")) or "Unknown",
                url=url,
            ),
            tickers=tickers,
            topics=topics,
        )
        key = _headline_key(headline)
        kept = earliest.get(key)
        if kept is None or published < kept.article.published_at:
            earliest[key] = found
    return sorted(earliest.values(), key=lambda item: item.article.published_at, reverse=True)


async def fetch_feed() -> list[FeedArticle]:
    """The latest news across the market, from one shared, cached request.

    Raises:
        ProviderNotConfiguredError: If the user has no Alpha Vantage key, or it is rejected.
        ProviderPlanError: If the key's plan does not cover Alpha Vantage's news.
        DataFetchError: If Alpha Vantage fails or the key's daily limit is reached.
    """
    api_key = alpha_vantage_key()
    if api_key is None:
        raise ProviderNotConfiguredError("No Alpha Vantage key.")
    if await news_refused(api_key):
        raise ProviderPlanError("News from Alpha Vantage needs a paid plan.")
    try:
        payload = await alpha_vantage_get(FUNCTION, dict(FEED_PARAMS), api_key)
    except ProviderPlanError:
        key = _refused_key(api_key)
        _refused[key] = time.monotonic()
        await cache_set(key, "1", REFUSED_SECONDS)
        raise ProviderPlanError("News from Alpha Vantage needs a paid plan.") from None
    return parse_feed(payload)


def market_news_from_feed(
    feed: list[FeedArticle], category: MarketNewsCategory, now: datetime
) -> MarketNews:
    """The feed's articles in ``category`` from the last :data:`MARKET_DAYS` days."""
    topics = CATEGORY_TOPICS[category]
    start = now - timedelta(days=MARKET_DAYS)
    articles = [
        item.article
        for item in feed
        if item.article.published_at >= start
        and (topics is None or item.topics & topics)
        and (category != "forex" or any(t.startswith("FOREX:") for t in item.tickers))
    ]
    return MarketNews(
        category=category, articles=articles, fetched_at=now, provider="alpha_vantage"
    )


def company_news_from_feed(feed: list[FeedArticle], symbol: str, now: datetime) -> CompanyNews:
    """The feed's articles about ``symbol`` (at least :data:`MIN_RELEVANCE` relevant) from
    the last :data:`COMPANY_DAYS` days, newest first. A company the latest thousand articles
    do not mention gets none, so this covers the most talked-about companies best."""
    symbol = symbol.strip().upper()
    end: date = market_day(now)
    start = end - timedelta(days=COMPANY_DAYS - 1)
    window = datetime.combine(start, datetime.min.time(), MARKET_ZONE)
    articles = [
        item.article
        for item in feed
        if item.tickers.get(symbol, 0.0) >= MIN_RELEVANCE and item.article.published_at >= window
    ]
    return CompanyNews(
        symbol=symbol,
        start=start,
        end=end,
        articles=articles,
        fetched_at=now,
        provider="alpha_vantage",
    )
