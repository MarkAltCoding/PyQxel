"""Tests for company news: the Finnhub client, its caching and fallback, the overview, and
the news route.

Finnhub is reached through a mock transport, so no test touches the network.
"""

from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.v1.endpoints import news as news_route
from app.core.credentials import ProviderKeys, use_provider_keys
from app.data import news
from app.data.fetcher import finnhub_symbol
from app.data.news import (
    NewsFetchError,
    NewsNotConfiguredError,
    NewsPlanError,
    NewsRateLimitError,
    fetch_company_news,
    parse_articles,
)
from app.main import app
from app.models.news import CompanyNews, NewsArticle
from app.stats.news import CONTEXT_ARTICLES, news_context, summarize_news

KEY = "fh-user-key"
NOW = datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)
WINDOW_START = datetime(2026, 10, 9, tzinfo=timezone.utc)

Handler = Callable[[httpx2.Request], httpx2.Response]


def _row(hours_ago: float, headline: str, source: str = "Reuters", **extra: Any) -> dict[str, Any]:
    """A Finnhub company news row published ``hours_ago`` before :data:`NOW`."""
    return {
        "category": "company",
        "datetime": int((NOW - timedelta(hours=hours_ago)).timestamp()),
        "headline": headline,
        "id": abs(hash(headline)) % 10_000_000,
        "image": "",
        "related": "AAPL",
        "source": source,
        "summary": f"About {headline.lower()}.",
        "url": f"https://news.example.com/{abs(hash(headline)) % 1000}",
    } | extra


ROWS = [
    _row(1, "Apple unveils new chips"),
    _row(5, "Apple Unveils New Chips!", source="Yahoo"),
    _row(30, "Apple shares slip", source="Yahoo", summary="Apple shares slip"),
    _row(24 * 10, "Apple faces probe", source="Bloomberg"),
]


def _finnhub(
    answer: Callable[[httpx2.Request], httpx2.Response] | None = None,
) -> tuple[httpx2.AsyncClient, list[httpx2.Request]]:
    """A client routed to a fake Finnhub serving :data:`ROWS`, and the requests it saw."""
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if answer is not None:
            return answer(request)
        return httpx2.Response(200, json=ROWS)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), requests


@pytest.fixture(autouse=True)
def finnhub_user(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Act as a user with a Finnhub key, at :data:`NOW`."""
    monkeypatch.setattr(news, "_now", lambda: NOW)
    news.clear_news_memory()
    use_provider_keys(ProviderKeys(finnhub=SecretStr(KEY)))
    yield
    use_provider_keys(ProviderKeys())
    news.clear_news_memory()


# Parsing.


def test_articles_are_newest_first_with_one_copy_of_each_headline() -> None:
    """Syndicated copies of a headline collapse to the earliest one."""
    articles = parse_articles(
        [*ROWS, _row(2, "apple unveils new chips", source="Benzinga")], WINDOW_START
    )

    assert [(a.headline, a.source) for a in articles] == [
        ("Apple Unveils New Chips!", "Yahoo"),
        ("Apple shares slip", "Yahoo"),
        ("Apple faces probe", "Bloomberg"),
    ]


def test_summaries_repeating_the_headline_are_dropped_and_long_ones_cut() -> None:
    """A summary that only restates the headline is no summary; a long one is shortened."""
    long = "word " * 400
    articles = parse_articles(
        [_row(1, "Apple shares slip", summary="Apple shares slip"), _row(2, "Long", summary=long)],
        WINDOW_START,
    )

    assert articles[0].summary is None
    assert articles[1].summary is not None
    assert len(articles[1].summary) <= news.SUMMARY_MAX_CHARS + 1
    assert articles[1].summary.endswith("…")


def test_rows_without_headline_link_or_time_in_the_window_are_skipped() -> None:
    """Malformed rows and articles from before the window are left out."""
    rows: list[Any] = [
        _row(1, ""),
        _row(1, "No link", url="javascript:alert(1)"),
        _row(1, "No time", datetime=0),
        _row(24 * 30, "Too old"),
        "not a row",
        _row(1, "Kept"),
    ]

    assert [a.headline for a in parse_articles(rows, WINDOW_START)] == ["Kept"]


def test_unexpected_payload_is_an_error() -> None:
    """Finnhub answering with something other than a list is a fetch error."""
    with pytest.raises(NewsFetchError):
        parse_articles({"error": "bad"}, WINDOW_START)


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [("aapl", "AAPL"), ("BRK-B", "BRK.B"), ("RY.TO", "RY.TO"), (" msft ", "MSFT")],
)
def test_symbols_are_named_as_finnhub_names_them(symbol: str, expected: str) -> None:
    """Share classes take a dot, and listings with an exchange suffix are kept."""
    assert finnhub_symbol(symbol) == expected


# Fetching.


@pytest.mark.asyncio
async def test_fetches_two_weeks_with_the_key_in_a_header() -> None:
    """The window ends today, and the key never appears in the URL."""
    client, requests = _finnhub()

    result = await fetch_company_news("aapl", client=client)

    assert (result.symbol, result.start, result.end) == ("AAPL", date(2026, 10, 9), NOW.date())
    assert len(result.articles) == 3
    (request,) = requests
    assert request.url.params["symbol"] == "AAPL"
    assert (request.url.params["from"], request.url.params["to"]) == ("2026-10-09", "2026-10-22")
    assert request.headers["X-Finnhub-Token"] == KEY
    assert KEY not in str(request.url)


@pytest.mark.asyncio
async def test_news_is_reused_while_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second request within ten minutes is served without asking Finnhub."""
    client, requests = _finnhub()
    await fetch_company_news("AAPL", client=client)
    monkeypatch.setattr(news, "_now", lambda: NOW + timedelta(minutes=5))
    await fetch_company_news("AAPL", client=client)
    monkeypatch.setattr(news, "_now", lambda: NOW + timedelta(minutes=11))
    await fetch_company_news("AAPL", client=client)

    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        lambda request: httpx2.Response(429, headers={"retry-after": "30"}),
        lambda request: httpx2.Response(500),
    ],
)
async def test_kept_news_stands_in_when_finnhub_fails(
    monkeypatch: pytest.MonkeyPatch, failure: Handler
) -> None:
    """Rate limits and outages serve the last answer, marked stale."""
    client, _ = _finnhub()
    fresh = await fetch_company_news("AAPL", client=client)
    monkeypatch.setattr(news, "_now", lambda: NOW + timedelta(hours=1))
    failing, _ = _finnhub(failure)

    stale = await fetch_company_news("AAPL", client=failing)

    assert stale.stale
    assert stale.articles == fresh.articles


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, NewsNotConfiguredError),
        (403, NewsPlanError),
        (429, NewsRateLimitError),
        (502, NewsFetchError),
    ],
)
async def test_errors_are_classified(status: int, error: type[Exception]) -> None:
    """Rejected keys, plan limits, rate limits and outages raise distinct errors."""
    client, _ = _finnhub(lambda request: httpx2.Response(status, json={"error": "x"}))

    with pytest.raises(error):
        await fetch_company_news("AAPL", client=client)


@pytest.mark.asyncio
async def test_rate_limit_carries_retry_after() -> None:
    """Finnhub's Retry-After is passed on."""
    client, _ = _finnhub(lambda request: httpx2.Response(429, headers={"retry-after": "12"}))

    with pytest.raises(NewsRateLimitError) as caught:
        await fetch_company_news("AAPL", client=client)

    assert caught.value.retry_after == 12


@pytest.mark.asyncio
async def test_unreachable_finnhub_is_a_fetch_error() -> None:
    """A transport failure with nothing kept is a fetch error."""

    def unreachable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    client, _ = _finnhub(unreachable)

    with pytest.raises(NewsFetchError):
        await fetch_company_news("AAPL", client=client)


@pytest.mark.asyncio
async def test_rejected_key_is_not_masked_by_kept_news(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected key is reported even when earlier news is kept."""
    client, _ = _finnhub()
    await fetch_company_news("AAPL", client=client)
    monkeypatch.setattr(news, "_now", lambda: NOW + timedelta(hours=1))
    rejecting, _ = _finnhub(lambda request: httpx2.Response(401))

    with pytest.raises(NewsNotConfiguredError):
        await fetch_company_news("AAPL", client=rejecting)


@pytest.mark.asyncio
async def test_without_a_key_nothing_is_requested() -> None:
    """A user without a Finnhub key is told to add one, and Finnhub is not asked."""
    use_provider_keys(ProviderKeys())
    client, requests = _finnhub()

    with pytest.raises(NewsNotConfiguredError):
        await fetch_company_news("AAPL", client=client)

    assert requests == []


@pytest.mark.asyncio
async def test_indexes_have_no_news() -> None:
    """Index symbols are answered with no articles, without a request."""
    client, requests = _finnhub()

    result = await fetch_company_news("^GSPC", client=client)

    assert result.articles == [] and requests == []


# Overview and context.


def _news(hours: list[float], sources: list[str] | None = None) -> CompanyNews:
    articles = [
        NewsArticle(
            published_at=NOW - timedelta(hours=h),
            headline=f"Story {i}",
            summary="s " * 300,
            source=(sources or ["Reuters"] * len(hours))[i],
            url=f"https://news.example.com/{i}",
        )
        for i, h in enumerate(hours)
    ]
    return CompanyNews(
        symbol="AAPL", start=date(2026, 10, 9), end=NOW.date(), articles=articles, fetched_at=NOW
    )


def test_overview_counts_by_day_source_and_recency() -> None:
    """Every day of the window is listed, and the last day and week are counted."""
    overview = summarize_news(
        _news([1, 2, 30, 24 * 8, 24 * 8 + 1], ["Reuters", "Yahoo", "Reuters", "Reuters", "WSJ"]),
        NOW,
    )

    assert overview.total_articles == 5
    assert (overview.last_day_articles, overview.last_week_articles) == (2, 3)
    assert len(overview.daily) == 14 and overview.daily[0].day == date(2026, 10, 9)
    assert sum(day.articles for day in overview.daily) == 5
    assert overview.busiest_day is not None and overview.busiest_day.day == NOW.date()
    assert [(s.source, s.articles) for s in overview.sources][0] == ("Reuters", 3)
    assert overview.daily_average == pytest.approx(5 / 14)
    assert overview.latest_at == NOW - timedelta(hours=1)


def test_overview_of_no_news() -> None:
    """Without articles there is no busiest day or latest article."""
    overview = summarize_news(_news([]), NOW)

    assert overview.total_articles == 0
    assert overview.busiest_day is None and overview.latest_at is None
    assert overview.sources == []


def test_context_gives_the_newest_articles_briefly() -> None:
    """Claude sees the newest articles with shortened summaries, and the full count."""
    context = news_context(_news([float(h) for h in range(CONTEXT_ARTICLES + 10)]), NOW)

    assert context.total_articles == CONTEXT_ARTICLES + 10
    assert len(context.articles) == CONTEXT_ARTICLES
    assert context.articles[0].headline == "Story 0"
    assert context.articles[0].summary is not None and len(context.articles[0].summary) <= 301


# The route.

client = TestClient(app)


def test_route_returns_overview_and_newest_articles(
    user: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route counts every article but lists only ``limit``."""

    async def fake_news(symbol: str) -> CompanyNews:
        return _news([1, 2, 3])

    monkeypatch.setattr(news_route, "fetch_company_news", fake_news)

    response = client.get("/api/v1/stocks/AAPL/news", params={"limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["overview"]["total_articles"] == 3
    assert [a["headline"] for a in body["articles"]] == ["Story 0", "Story 1"]
    assert body["notice"] is None


def test_route_notes_stale_news(user: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kept news is served with a notice of when it was fetched."""

    async def fake_news(symbol: str) -> CompanyNews:
        return _news([1]).model_copy(update={"stale": True})

    monkeypatch.setattr(news_route, "fetch_company_news", fake_news)

    body = client.get("/api/v1/stocks/AAPL/news").json()

    assert "2026-10-22 15:00 UTC" in body["notice"]


@pytest.mark.parametrize(
    ("error", "status", "retry_after"),
    [
        (NewsNotConfiguredError("no key"), 403, None),
        (NewsPlanError("plan"), 402, None),
        (NewsRateLimitError("limited", 30), 503, "30"),
        (NewsFetchError("down"), 502, None),
    ],
)
def test_route_errors(
    user: object,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
    retry_after: str | None,
) -> None:
    """Finnhub failures map to the statuses the docs give."""

    async def failing(symbol: str) -> CompanyNews:
        raise error

    monkeypatch.setattr(news_route, "fetch_company_news", failing)

    response = client.get("/api/v1/stocks/AAPL/news")

    assert response.status_code == status
    assert response.headers.get("retry-after") == retry_after
