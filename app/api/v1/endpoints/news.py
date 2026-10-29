"""News routes: a company's recent news from Finnhub, on the user's own key, and an
overview of it."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.v1.endpoints.stocks import Symbol
from app.data.news import (
    NewsFetchError,
    NewsNotConfiguredError,
    NewsPlanError,
    NewsRateLimitError,
    fetch_company_news,
)
from app.models.news import NewsResponse
from app.stats.news import summarize_news

router = APIRouter()


def news_http_error(exc: NewsFetchError) -> HTTPException:
    """Translate a Finnhub failure into an HTTP error.

    403 without a usable Finnhub key, 402 when the plan does not cover the company, 503
    when the key is rate limited (with ``Retry-After`` when known), and 502 otherwise.
    """
    if isinstance(exc, NewsNotConfiguredError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, NewsPlanError):
        return HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc))
    if isinstance(exc, NewsRateLimitError):
        headers = None if exc.retry_after is None else {"Retry-After": str(exc.retry_after)}
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc), headers=headers
        )
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/{symbol}/news", response_model=NewsResponse, summary="Recent company news")
async def get_news(
    symbol: Symbol,
    limit: Annotated[int, Query(ge=1, le=200, description="Articles to return.")] = 50,
) -> NewsResponse:
    """Return the last two weeks of news about ``symbol`` from Finnhub, newest first.

    ``overview`` counts every article in the window by day and by publisher, while
    ``articles`` holds the newest ``limit`` of them. Copies of a syndicated story under
    the same headline count once. News is fetched on your own Finnhub key
    (``X-Finnhub-Key``), whose free plan covers North American companies, and
    reused for ten minutes. When Finnhub cannot be reached, the last news fetched in the
    past day is returned with a ``notice``.

    Returns 403 without a valid Finnhub key, 402 when your plan does not cover the
    company, 503 when Finnhub rate limits your key, and 502 when Finnhub fails.
    """
    try:
        news = await fetch_company_news(symbol)
    except NewsFetchError as exc:
        raise news_http_error(exc) from exc
    notice = None
    if news.stale:
        notice = (
            f"Finnhub could not be reached, so this is the news as of "
            f"{news.fetched_at:%Y-%m-%d %H:%M} UTC."
        )
    return NewsResponse(
        symbol=news.symbol,
        start=news.start,
        end=news.end,
        overview=summarize_news(news, datetime.now(timezone.utc)),
        articles=news.articles[:limit],
        fetched_at=news.fetched_at,
        notice=notice,
    )
