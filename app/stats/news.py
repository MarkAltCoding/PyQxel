"""Summaries of a company's news: how much it drew, when, and from whom."""

from collections import Counter
from datetime import datetime, timedelta

from app.models.news import (
    CompanyNews,
    NewsContext,
    NewsDay,
    NewsHeadline,
    NewsOverview,
    NewsSource,
)

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
