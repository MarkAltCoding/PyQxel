"""News from Alpha Vantage's one shared feed, where Finnhub cannot supply it."""

import time
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import SecretStr

from app.api.v1.endpoints import markets as markets_route
from app.api.v1.endpoints import picks as picks_route
from app.core.credentials import ProviderKeys, use_provider_keys
from app.data import alpha_vantage_news, fetcher, markets, news
from app.data.fetcher import ProviderPlanError
from app.data.news import NewsNotConfiguredError, fetch_company_news
from app.models.markets import FomcMeeting
from app.stats.markets import market_context
from app.stats.picks import headlines_each

NOW = datetime.now(timezone.utc)


def _stamp(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y%m%dT%H%M%S")


FEED: dict[str, Any] = {
    "items": "3",
    "feed": [
        {
            "title": "Apple beats on earnings and lifts its outlook",
            "url": "https://example.com/apple",
            "time_published": _stamp(2),
            "summary": "Apple reported results above estimates.",
            "source": "Reuters",
            "topics": [{"topic": "earnings", "relevance_score": "0.9"}],
            "ticker_sentiment": [
                {"ticker": "AAPL", "relevance_score": "0.91"},
                {"ticker": "MSFT", "relevance_score": "0.05"},
            ],
        },
        {
            "title": "Berkshire agrees to buy a railroad",
            "url": "https://example.com/brk",
            "time_published": _stamp(5),
            "summary": "",
            "source": "Bloomberg",
            "topics": [{"topic": "mergers_and_acquisitions", "relevance_score": "0.8"}],
            "ticker_sentiment": [{"ticker": "BRK.B", "relevance_score": "0.7"}],
        },
        {
            "title": "Apple beats on earnings and lifts its outlook",
            "url": "https://example.com/apple-copy",
            "time_published": _stamp(1),
            "summary": "A syndicated copy.",
            "source": "Aggregator",
            "topics": [],
            "ticker_sentiment": [{"ticker": "AAPL", "relevance_score": "0.9"}],
        },
    ],
}


@pytest.fixture(autouse=True)
def feed(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """An Alpha Vantage key and no Finnhub key; record each request sent."""
    sent: list[str] = []

    async def get(function: str, params: dict[str, str], api_key: str, client: Any = None) -> Any:
        sent.append(function)
        return FEED

    monkeypatch.setattr(alpha_vantage_news, "alpha_vantage_get", get)
    alpha_vantage_news._refused.clear()
    news.clear_news_memory()
    use_provider_keys(ProviderKeys(alpha_vantage=SecretStr("av-key")))
    yield sent
    use_provider_keys(ProviderKeys())


def test_the_feed_is_read_once_each_headline_and_share_classes_as_written() -> None:
    articles = alpha_vantage_news.parse_feed(FEED)

    assert [a.article.headline for a in articles] == [
        "Apple beats on earnings and lifts its outlook",
        "Berkshire agrees to buy a railroad",
    ]
    assert articles[0].article.url == "https://example.com/apple"  # The earliest copy.
    assert "BRK-B" in articles[1].tickers


def test_a_companys_news_is_what_the_feed_says_about_it(feed: list[str]) -> None:
    import asyncio

    async def both() -> tuple[Any, Any]:
        return await fetch_company_news("AAPL"), await fetch_company_news("MSFT")

    apple, microsoft = asyncio.run(both())

    assert apple.provider == "alpha_vantage" and len(apple.articles) == 1
    assert microsoft.articles == []  # A passing mention is not its news.
    assert feed == ["NEWS_SENTIMENT", "NEWS_SENTIMENT"]  # Cached by alpha_vantage_get itself.


def test_market_news_falls_back_to_the_feed_by_category() -> None:
    import asyncio

    general = asyncio.run(markets.fetch_any_market_news("general"))
    deals = asyncio.run(markets.fetch_any_market_news("merger"))

    assert general.provider == "alpha_vantage" and len(general.articles) == 2
    assert [a.headline for a in deals.articles] == ["Berkshire agrees to buy a railroad"]


def test_a_free_plan_is_asked_once_a_day(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    sent: list[str] = []

    async def refuse(
        function: str, params: dict[str, str], api_key: str, client: Any = None
    ) -> Any:
        sent.append(function)
        raise ProviderPlanError("premium")

    monkeypatch.setattr(alpha_vantage_news, "alpha_vantage_get", refuse)

    for _ in range(3):
        with pytest.raises(NewsNotConfiguredError):
            asyncio.run(fetch_company_news("AAPL"))

    assert sent == ["NEWS_SENTIMENT"]


def test_a_key_known_to_be_free_is_never_asked(
    monkeypatch: pytest.MonkeyPatch, feed: list[str]
) -> None:
    import asyncio

    monkeypatch.setattr(alpha_vantage_news, "alpha_vantage_free_history", lambda key: True)

    with pytest.raises(NewsNotConfiguredError):
        asyncio.run(fetch_company_news("AAPL"))

    assert feed == []


def test_the_headline_budget_is_shared_out_evenly() -> None:
    assert [headlines_each(n) for n in (1, 10, 20, 30, 40, 60, 100)] == [12, 12, 12, 8, 6, 4, 4]


def test_the_calendar_follows_the_holding_period() -> None:
    assert markets_route.backdrop_days(14) == 28
    assert markets_route.backdrop_days(60) == 60
    assert markets_route.backdrop_days(3 * 365) == 90
    assert markets_route.backdrop_days(None) == 90
    assert markets_route.backdrop_finnhub_requests(90) == 20


def test_fomc_meetings_run_through_the_horizon() -> None:
    today = date(2026, 11, 20)
    meetings = [
        FomcMeeting(
            start=today + timedelta(days=d), end=today + timedelta(days=d + 1), projections=False
        )
        for d in (20, 80, 140, 200, 400)
    ]

    short = market_context(today, meetings, None, None, None, None, None)
    year = market_context(today, meetings, None, None, None, None, None, fomc_days=365)

    assert len(short.fomc_meetings) == 2
    assert len(year.fomc_meetings) == 4


def test_news_comes_first_and_the_calendar_takes_the_minute_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No Finnhub key: the shared feed costs nothing per company, and the calendar is whole.
    assert picks_route.plan_requests(40, 365) == (40, 90)

    use_provider_keys(ProviderKeys(finnhub=SecretStr("f")))
    monkeypatch.setattr(fetcher, "finnhub_minute_limit", lambda key: None)

    # 55 a minute, 2 kept free, 7 for the IPOs, holidays, status, and news categories.
    assert picks_route.plan_requests(30, 365) == (30, 90)  # 16 weeks left: a full quarter.
    assert picks_route.plan_requests(40, 365) == (40, 42)  # Six weeks left.
    assert picks_route.plan_requests(40, 14) == (40, 28)
    assert picks_route.plan_requests(60, 365) == (42, 28)  # The calendar keeps four weeks.


def test_an_estimate_counts_the_shared_feed_once() -> None:
    import asyncio

    from app.data.estimates import estimate

    fetches = [("news", ["AAPL", "MSFT", "XOM"], "1y", "1d"), ("market_news", [], "1y", "1d")]

    requests, _, _ = asyncio.run(estimate(fetches))
    assert requests["alpha_vantage"] == 1

    alpha_vantage_news._refused[alpha_vantage_news._refused_key("av-key")] = time.monotonic()
    requests, _, _ = asyncio.run(estimate(fetches))
    assert requests["alpha_vantage"] == 0
