"""Company news from Finnhub, on the requesting user's own key.

Finnhub's company news endpoint lists the articles that mention a company over a date
range; its free plan covers North American companies, a year back, at 60 requests a
minute. One window, the last :data:`NEWS_DAYS` days, is fetched per company and serves
both the stock page and the analyses, so viewing a stock and asking for its report costs
one request.

Answers are kept for :data:`NEWS_KEEP_SECONDS` in memory and in Redis, under the
provider's name, so they are only served to users who query Finnhub themselves, and are
reused for :data:`NEWS_FRESH_SECONDS`. When Finnhub fails or rate limits the key, a kept
answer is served instead, marked ``stale``.
"""

import html
import logging
import re
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx2

from app.core.cache import cache_get, cache_set
from app.data.fetcher import (
    DataFetchError,
    FinnhubRateLimitError,
    ProviderNotConfiguredError,
    ProviderPlanError,
    finnhub_get,
    finnhub_key,
    finnhub_symbol,
)
from app.models.news import CompanyNews, NewsArticle

logger = logging.getLogger(__name__)

NEWS_DAYS: int = 14
"""Days of news fetched for a company, today included."""
NEWS_FRESH_SECONDS: float = 10 * 60
"""How long fetched news is reused before Finnhub is asked again."""
NEWS_KEEP_SECONDS: float = 24 * 60 * 60
"""How long fetched news is kept to stand in when Finnhub cannot be reached."""
NEWS_MEMORY_SIZE: int = 256
"""Companies whose news is kept in process memory, for deployments without Redis."""
MAX_ARTICLES: int = 500
"""Articles kept per company, the newest; busy large caps can draw more in two weeks."""
SUMMARY_MAX_CHARS: int = 600
"""Length a publisher's summary is cut to."""

_memory: "OrderedDict[str, str]" = OrderedDict()
"""Cache key -> CompanyNews JSON."""


class NewsFetchError(RuntimeError):
    """Raised when news cannot be fetched."""


class NewsNotConfiguredError(NewsFetchError):
    """Raised when the user has no Finnhub key, or Finnhub rejects it."""


class NewsPlanError(NewsFetchError):
    """Raised when the user's Finnhub plan does not cover the company."""


class NewsRateLimitError(NewsFetchError):
    """Raised when Finnhub rate limits the user's key."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def clear_news_memory() -> None:
    """Forget the news kept in process memory."""
    _memory.clear()


_SPACE = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]*>")
_WORD = re.compile(r"[a-z0-9]+")


def _clean(text: object) -> str:
    """``text`` without HTML tags or entities, and with runs of whitespace collapsed; empty
    for anything not a string. Some feeds send summaries as HTML lists of links."""
    if not isinstance(text, str):
        return ""
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", text))).strip()


def _shorten(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` characters at a word boundary."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def _headline_key(headline: str) -> str:
    """What two copies of a syndicated headline share: its words, in lower case."""
    return " ".join(_WORD.findall(headline.lower()))


def _article(row: Any, start: datetime) -> NewsArticle | None:
    """Read one Finnhub row, or ``None`` when it lacks a headline, link or time in the window."""
    if not isinstance(row, dict):
        return None
    headline, url = _clean(row.get("headline")), _clean(row.get("url"))
    source = _clean(row.get("source")) or "Unknown"
    # Aggregated wire stories end with their publisher, which is already the source.
    headline = headline.removesuffix(f" - {source}")
    stamp = row.get("datetime")
    if not headline or not url.startswith(("https://", "http://")):
        return None
    if not isinstance(stamp, (int, float)) or isinstance(stamp, bool) or stamp <= 0:
        return None
    published = datetime.fromtimestamp(stamp, timezone.utc)
    if published < start:
        return None
    summary = _clean(row.get("summary"))
    if _headline_key(summary) == _headline_key(headline):
        summary = ""
    return NewsArticle(
        published_at=published,
        headline=headline,
        summary=_shorten(summary, SUMMARY_MAX_CHARS) or None,
        source=source,
        url=url,
        category=_clean(row.get("category")) or None,
    )


def parse_articles(payload: Any, start: datetime) -> list[NewsArticle]:
    """Finnhub's rows as articles, newest first, keeping the first copy of each headline.

    Aggregators republish the same story under the same headline, so only its earliest
    copy is kept.
    """
    if not isinstance(payload, list):
        raise NewsFetchError("Finnhub returned news in an unexpected form.")
    earliest: dict[str, NewsArticle] = {}
    for row in payload:
        article = _article(row, start)
        if article is None:
            continue
        key = _headline_key(article.headline)
        kept = earliest.get(key)
        if kept is None or article.published_at < kept.published_at:
            earliest[key] = article
    articles = sorted(earliest.values(), key=lambda article: article.published_at, reverse=True)
    return articles[:MAX_ARTICLES]


async def _request(
    symbol: str, start: date, end: date, api_key: str, client: httpx2.AsyncClient | None
) -> Any:
    """GET Finnhub's company news, as news errors."""
    params = {"symbol": finnhub_symbol(symbol), "from": start.isoformat(), "to": end.isoformat()}
    try:
        return await finnhub_get("company-news", params, api_key, client)
    except ProviderNotConfiguredError as exc:
        raise NewsNotConfiguredError(str(exc)) from exc
    except ProviderPlanError as exc:
        raise NewsPlanError(f"Your Finnhub plan does not cover news about {symbol}.") from exc
    except FinnhubRateLimitError as exc:
        raise NewsRateLimitError(str(exc), exc.retry_after) from exc
    except DataFetchError as exc:
        raise NewsFetchError(str(exc)) from exc


async def _kept(key: str) -> CompanyNews | None:
    """The news kept under ``key``, from memory or Redis."""
    text = _memory.get(key)
    if text is None:
        text = await cache_get(key)
    if text is None:
        return None
    try:
        return CompanyNews.model_validate_json(text)
    except ValueError:
        logger.warning("Ignoring unreadable kept news under %s.", key)
        return None


async def _keep(key: str, news: CompanyNews) -> None:
    text = news.model_dump_json()
    _memory[key] = text
    _memory.move_to_end(key)
    while len(_memory) > NEWS_MEMORY_SIZE:
        _memory.popitem(last=False)
    await cache_set(key, text, NEWS_KEEP_SECONDS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def fetch_company_news(symbol: str, client: httpx2.AsyncClient | None = None) -> CompanyNews:
    """Fetch the last :data:`NEWS_DAYS` days of news about ``symbol``.

    Args:
        symbol: Ticker symbol, e.g. ``"AAPL"``. Case and surrounding whitespace are ignored.
        client: Optional shared HTTP client. A short-lived one is created when omitted.

    Returns:
        The articles, newest first; none for an unknown symbol or an index. When Finnhub
        fails or rate limits the key, the last answer kept, with ``stale`` set.

    Raises:
        NewsNotConfiguredError: If the user has no Finnhub key, or it is rejected.
        NewsPlanError: If the user's plan does not cover the company.
        NewsRateLimitError: If the key is rate limited and no earlier answer is kept.
        NewsFetchError: If Finnhub fails and no earlier answer is kept.
    """
    symbol = symbol.strip().upper()
    api_key = finnhub_key()
    if api_key is None:
        raise NewsNotConfiguredError(
            "Company news comes from Finnhub on your own key: add it in Settings."
        )
    now = _now()
    end = now.date()
    start = end - timedelta(days=NEWS_DAYS - 1)
    key = f"news:finnhub:{symbol}"
    kept = await _kept(key)
    if (
        kept is not None
        and kept.end == end
        and (now - kept.fetched_at).total_seconds() < NEWS_FRESH_SECONDS
    ):
        return kept
    if symbol.startswith("^"):
        return CompanyNews(symbol=symbol, start=start, end=end, articles=[], fetched_at=now)
    try:
        payload = await _request(symbol, start, end, api_key, client)
        window_start = datetime.combine(start, datetime.min.time(), timezone.utc)
        news = CompanyNews(
            symbol=symbol,
            start=start,
            end=end,
            articles=parse_articles(payload, window_start),
            fetched_at=now,
        )
    except NewsNotConfiguredError, NewsPlanError:
        raise
    except NewsFetchError as exc:
        if kept is None:
            raise
        logger.warning("Serving kept news for %s: %s", symbol, exc)
        return kept.model_copy(update={"stale": True})
    await _keep(key, news)
    return news
