"""Summaries of a company's news: how much it drew, when, and from whom."""

import math
import re
from collections import Counter
from datetime import datetime, timedelta

from app.models.news import (
    CompanyNews,
    NewsArticle,
    NewsContext,
    NewsDay,
    NewsHeadline,
    NewsOverview,
    NewsSource,
)
from app.stats.markets import MAJOR_PUBLISHERS

TOP_SOURCES: int = 8
"""Publishers listed in an overview."""
CONTEXT_ARTICLES: int = 40
"""Articles given to Claude, the newest."""
CONTEXT_SUMMARY_CHARS: int = 300
"""Length each summary given to Claude is cut to."""


def summarize_news(news: CompanyNews, now: datetime) -> NewsOverview:
    """Count ``news``'s articles by day and by publisher, and over the last day and week."""
    by_day = Counter(article.published_at.date() for article in news.articles)
    days = (news.end - news.start).days + 1
    daily = [
        NewsDay(day=day, articles=by_day[day])
        for day in (news.start + timedelta(days=offset) for offset in range(days))
    ]
    # Ties go to the latest day.
    busiest = max(reversed(daily), key=lambda entry: entry.articles, default=None)
    sources = Counter(article.source for article in news.articles)
    return NewsOverview(
        total_articles=len(news.articles),
        last_day_articles=sum(
            now - article.published_at <= timedelta(days=1) for article in news.articles
        ),
        last_week_articles=sum(
            now - article.published_at <= timedelta(days=7) for article in news.articles
        ),
        daily_average=len(news.articles) / days,
        busiest_day=busiest if busiest is not None and busiest.articles > 0 else None,
        latest_at=news.articles[0].published_at if news.articles else None,
        sources=[
            NewsSource(source=source, articles=count)
            for source, count in sources.most_common(TOP_SOURCES)
        ],
        daily=daily,
    )


def _shorten(text: str | None) -> str | None:
    if text is None or len(text) <= CONTEXT_SUMMARY_CHARS:
        return text
    return text[:CONTEXT_SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def news_context(news: CompanyNews, now: datetime) -> NewsContext:
    """The newest of ``news``'s articles, as given to Claude, with the window's counts."""
    overview = summarize_news(news, now)
    return NewsContext(
        start=news.start,
        end=news.end,
        total_articles=overview.total_articles,
        last_week_articles=overview.last_week_articles,
        articles=[
            NewsHeadline(
                published_at=article.published_at,
                source=article.source,
                headline=article.headline,
                summary=_shorten(article.summary),
            )
            for article in news.articles[:CONTEXT_ARTICLES]
        ],
    )


COMPANY_TOPICS: tuple[tuple[re.Pattern[str], float], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), weight)
    for pattern, weight in (
        # Results and the outlook move a company's shares most.
        (r"\b(earnings|results|quarter(ly)?|revenue|sales|profit|loss|eps)\b", 1.0),
        (r"\b(guidance|outlook|forecast|beats?|miss(es)?|tops?|falls short)\b", 1.2),
        # Analysts' views.
        (r"\b(upgrades?|downgrades?|price target|initiat\w+ coverage|rating)\b", 1.0),
        # Deals and the company's structure.
        (r"\b(merger|acquir\w+|buyout|takeover|agrees? to buy|deal|spin-?off)\b", 1.2),
        (r"\b(buyback|repurchase|dividend|stock split|offering|bankruptcy|delist\w*)\b", 1.0),
        # Leadership, legal and regulatory trouble.
        (r"\b(ceo|cfo|chief executive|resigns?|steps down|appoint\w*|layoffs?|job cuts)\b", 0.8),
        (r"\b(lawsuit|sued|probe|investigation|sec|antitrust|fine[sd]?|settle\w*|recall)\b", 0.9),
        (r"\b(fda|approval|approves?|clinical trial|patent)\b", 0.8),
        # Big moves in the shares themselves.
        (r"\b(shares|stock)\b.*\b(jump|soar|surge|plunge|tumble|sink|slump|rall(y|ies))\w*", 0.8),
        (r"\$\s?\d[\d,.]*\s?(billion|bn|trillion|tn)\b", 0.5),
    )
)
"""Topics that make a company's headline matter more for its shares, with how much each
adds."""

COMPANY_TOPIC_SATURATION: float = 2.0
"""Topic weight at which a company headline counts as fully on topic."""

NEWS_HALF_LIFE_HOURS: float = 48.0
"""Hours after which a company headline's recency counts half; company news is sparser
than market news, so it fades more slowly."""


def company_headline_importance(article: NewsArticle, symbol: str, now: datetime) -> float:
    """Score how much ``article`` looks to matter for ``symbol``'s shares, from 0 to 1.

    A heuristic, not a model: the company events its headline names (results and
    guidance, analyst ratings, deals, leadership, legal and regulatory news), whether it
    names the ticker, whether a major publisher ran it, and how recent it is, halving every
    :data:`NEWS_HALF_LIFE_HOURS` hours.
    """
    topics = sum(weight for pattern, weight in COMPANY_TOPICS if pattern.search(article.headline))
    if re.search(rf"\b{re.escape(symbol)}\b", article.headline):
        topics += 0.5
    relevance = min(topics / COMPANY_TOPIC_SATURATION, 1.0)
    publisher = 1.0 if article.source.strip().lower() in MAJOR_PUBLISHERS else 0.4
    hours = max((now - article.published_at).total_seconds() / 3600.0, 0.0)
    recency = math.pow(0.5, hours / NEWS_HALF_LIFE_HOURS)
    return round((0.6 * relevance + 0.4 * publisher) * (0.4 + 0.6 * recency), 4)


def score_company_news(
    articles: list[NewsArticle], symbol: str, now: datetime
) -> list[NewsArticle]:
    """``articles`` with each one's :func:`company_headline_importance`, in the same order."""
    return [
        article.model_copy(update={"importance": company_headline_importance(article, symbol, now)})
        for article in articles
    ]
