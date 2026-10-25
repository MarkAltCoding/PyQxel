"""Tests for market-wide data: the Fed's FOMC calendar, Finnhub's market news, earnings and
IPO calendars, holidays and status, the markets routes, and the market backdrop given to
analyses.

The Fed and Finnhub are reached through mock transports, so no test touches the network.
"""

from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.v1.endpoints import markets as markets_route
from app.core.config import Settings
from app.core.credentials import ProviderKeys, use_provider_keys
from app.data import fetcher, finnhub, fomc, markets
from app.data.fomc import FALLBACK_MEETINGS, fetch_fomc_meetings, parse_meetings
from app.data.markets import (
    fetch_earnings_calendar,
    fetch_ipo_calendar,
    fetch_market_holidays,
    fetch_market_news,
    fetch_market_status,
)
from app.main import app
from app.models.markets import EarningsEvent, IpoEvent, MarketHoliday, MarketNews
from app.models.news import NewsArticle
from app.stats.markets import major_earnings, market_context

KEY = "fh-user-key"
TODAY = date(2026, 10, 25)

FED_HTML = """<html><body>
<div class="panel panel-default"><div class="panel-heading"><h4>2026 FOMC Meetings</h4></div>
<div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>October</strong></div>
<div class="fomc-meeting__date">27-28</div></div>
<div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>December</strong></div>
<div class="fomc-meeting__date">8-9*</div></div>
<div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>March</strong></div>
<div class="fomc-meeting__date">15 (unscheduled)</div></div>
</div>
<div class="panel panel-default"><div class="panel-heading"><h4>2019 FOMC Meetings</h4></div>
<div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>Apr/May</strong></div>
<div class="fomc-meeting__date">30-1</div></div>
</div>
<div class="panel"><h4>Other things</h4></div>
</body></html>"""


def _finnhub(
    routes: dict[str, Callable[[httpx2.Request], httpx2.Response]],
) -> tuple[httpx2.AsyncClient, list[httpx2.Request]]:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return routes[request.url.path.removeprefix("/api/v1/")](request)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), requests


def _json(payload: Any) -> Callable[[httpx2.Request], httpx2.Response]:
    return lambda request: httpx2.Response(200, json=payload)


@pytest.fixture(autouse=True)
def finnhub_user(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Act as a user with a Finnhub key, the yfinance fallback off, and nothing kept."""
    monkeypatch.setattr(fetcher, "get_settings", lambda: Settings(yfinance_fallback=False))
    fetcher.clear_alpha_vantage_memory()
    finnhub.clear_signals_memory()
    fomc.clear_fomc_memory()
    use_provider_keys(ProviderKeys(finnhub=SecretStr(KEY)))
    yield
    use_provider_keys(ProviderKeys())


# The FOMC calendar.


def test_fed_calendar_is_read() -> None:
    """Meetings are read with their projections, across months, without unscheduled ones."""
    meetings = parse_meetings(FED_HTML)

    assert [(m.start, m.end, m.projections) for m in meetings] == [
        (date(2019, 4, 30), date(2019, 5, 1), False),
        (date(2026, 10, 27), date(2026, 10, 28), False),
        (date(2026, 12, 8), date(2026, 12, 9), True),
    ]


@pytest.mark.asyncio
async def test_fed_calendar_is_fetched_and_kept() -> None:
    """The page is read once and kept."""
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, text=FED_HTML)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    first = await fetch_fomc_meetings(client)
    second = await fetch_fomc_meetings(client)

    assert first == second and len(first) == 3
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_fed_calendar_falls_back_when_unreachable() -> None:
    """A failed fetch, or a page without meetings, gives the built-in schedule."""
    down = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(503)))
    changed = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda r: httpx2.Response(200, text="<html></html>"))
    )

    assert await fetch_fomc_meetings(down) == FALLBACK_MEETINGS
    assert await fetch_fomc_meetings(changed) == FALLBACK_MEETINGS


def test_fallback_schedule_is_ordered_and_covers_2027() -> None:
    """The built-in schedule runs to the end of 2027, eight meetings a year."""
    starts = [meeting.start for meeting in FALLBACK_MEETINGS]
    assert starts == sorted(starts)
    assert sum(meeting.start.year == 2027 for meeting in FALLBACK_MEETINGS) == 8


# Finnhub's market data.


@pytest.mark.asyncio
async def test_market_news_strips_html_and_old_articles() -> None:
    """Summaries sent as HTML lists read as text, and week-old articles are left out."""
    now = datetime.now(timezone.utc)
    rows = [
        {"category": "forex", "datetime": int(now.timestamp()) - 60, "headline": "Oil jumps",
         "source": "Forexlive", "url": "https://example.com/1",
         "summary": "<ul><li><a href='x'>Rig count &amp; prices</a></li></ul>"},
        {"category": "forex", "datetime": int((now - timedelta(days=9)).timestamp()),
         "headline": "Old news", "source": "Forexlive", "url": "https://example.com/2"},
    ]  # fmt: skip
    client, requests = _finnhub({"news": _json(rows)})

    news = await fetch_market_news("forex", client)

    assert requests[0].url.params["category"] == "forex"
    assert [a.headline for a in news.articles] == ["Oil jumps"]
    assert news.articles[0].summary == "Rig count & prices"


@pytest.mark.asyncio
async def test_earnings_calendar_splits_at_the_row_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A window that hits Finnhub's row limit is split until no part does."""
    monkeypatch.setattr(markets, "EARNINGS_ROW_LIMIT", 3)

    def answer(request: httpx2.Request) -> httpx2.Response:
        start = date.fromisoformat(request.url.params["from"])
        end = date.fromisoformat(request.url.params["to"])
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        rows = [
            {"symbol": f"S{day.day}{n}", "date": day.isoformat(), "hour": "amc",
             "quarter": 3, "year": 2026, "revenueEstimate": 1e9}
            for day in days for n in range(2)
        ]  # fmt: skip
        return httpx2.Response(200, json={"earningsCalendar": rows})

    client, requests = _finnhub({"calendar/earnings": answer})

    events = await fetch_earnings_calendar(TODAY, TODAY + timedelta(days=3), client)

    assert len(events) == 8
    assert all(len(r.url.params["from"]) == 10 for r in requests)
    assert len(requests) > 1
    assert [event.date for event in events] == sorted(event.date for event in events)


@pytest.mark.asyncio
async def test_ipos_holidays_and_status_are_read() -> None:
    """The IPO calendar, holidays and market status are read from Finnhub's answers."""
    client, _ = _finnhub(
        {
            "calendar/ipo": _json(
                {"ipoCalendar": [
                    {"date": "2026-10-28", "exchange": "NYSE", "name": "BIG CO INC",
                     "numberOfShares": 1e7, "price": "18.00-20.00", "status": "expected",
                     "symbol": "BIG", "totalSharesValue": 2e8},
                    {"date": "2026-10-27", "name": "Small Co", "status": "filed",
                     "totalSharesValue": 0},
                ]}  # fmt: skip
            ),
            "stock/market-holiday": _json(
                {"data": [
                    {"eventName": "Thanksgiving Day", "atDate": "2026-11-27",
                     "tradingHour": "09:30-13:00"},
                    {"eventName": "Thanksgiving Day", "atDate": "2026-11-26", "tradingHour": ""},
                ]}  # fmt: skip
            ),
            "stock/market-status": _json(
                {"exchange": "US", "isOpen": True, "session": "regular", "t": 1792699195}
            ),
        }
    )

    ipos = await fetch_ipo_calendar(TODAY, TODAY + timedelta(days=30), client)
    holidays = await fetch_market_holidays(client)
    status = await fetch_market_status(client)

    assert [(i.name, i.value) for i in ipos] == [("Small Co", None), ("Big Co Inc", 2e8)]
    assert [(h.date, h.trading_hours) for h in holidays] == [
        (date(2026, 11, 26), None),
        (date(2026, 11, 27), "09:30-13:00"),
    ]
    assert status.is_open and status.session == "regular"


def test_major_earnings_by_market_cap_or_revenue() -> None:
    """Companies in the universe count from $10B; others from $2B of quarterly revenue."""
    events = [
        EarningsEvent(symbol="AAPL", date=TODAY, revenue_estimate=1e11),
        EarningsEvent(symbol="TINY", date=TODAY, revenue_estimate=5e9),
        EarningsEvent(symbol="SAP", date=TODAY, revenue_estimate=9e9),
        EarningsEvent(symbol="NANO", date=TODAY, revenue_estimate=1e8),
    ]
    caps = {"AAPL": ("Apple Inc.", 3e12), "TINY": ("Tiny Corp", 5e8)}

    major = major_earnings(events, caps)

    assert [(e.symbol, e.name, e.market_cap) for e in major] == [
        ("AAPL", "Apple Inc.", 3e12),
        ("SAP", None, None),
    ]


def test_market_context_keeps_what_is_soon() -> None:
    """The context keeps the next two meetings and the next two weeks' largest events."""
    news = MarketNews(
        category="general",
        articles=[
            NewsArticle(
                published_at=datetime.now(timezone.utc),
                headline="Stocks rise",
                summary="word " * 100,
                source="Reuters",
                url="https://example.com/a",
            )
        ],
        fetched_at=datetime.now(timezone.utc),
    )
    context = market_context(
        TODAY,
        FALLBACK_MEETINGS,
        [
            MarketHoliday(date=date(2026, 11, 20), name="Within a month"),
            MarketHoliday(date=date(2026, 11, 26), name="Thanksgiving Day"),
        ],
        [
            EarningsEvent(symbol="SMALL", date=TODAY, market_cap=2e10),
            EarningsEvent(symbol="BIG", date=TODAY + timedelta(days=1), market_cap=3e12),
            EarningsEvent(symbol="LATER", date=TODAY + timedelta(days=20), market_cap=4e12),
        ],
        [
            IpoEvent(date=TODAY, name="Gone", status="withdrawn", value=1e9),
            IpoEvent(date=TODAY, name="Kept", status="expected", value=1e8),
        ],
        news,
        None,
    )

    assert [m.start for m in context.fomc_meetings] == [date(2026, 10, 27), date(2026, 12, 8)]
    assert [h.name for h in context.holidays] == ["Within a month"]
    assert [e.symbol for e in context.major_earnings] == ["BIG", "SMALL"]
    assert [i.name for i in context.ipos] == ["Kept"]
    assert context.headlines[0].summary is not None
    assert len(context.headlines[0].summary) <= 241


# The routes.

client = TestClient(app)


def _serve_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    async def earnings(start: date, end: date) -> list[EarningsEvent]:
        return [
            EarningsEvent(symbol="AAPL", date=start, revenue_estimate=1e11),
            EarningsEvent(symbol="NANO", date=start, revenue_estimate=1e6),
        ]

    async def ipos(start: date, end: date) -> list[IpoEvent]:
        return [IpoEvent(date=start, name="Big Co", status="expected")]

    async def holidays() -> list[MarketHoliday]:
        return [
            MarketHoliday(date=date(2020, 1, 1), name="Long ago"),
            MarketHoliday(date=date.today() + timedelta(days=2), name="Soon"),
        ]

    async def status() -> Any:
        raise fetcher.ProviderPlanError("not on the plan")

    monkeypatch.setattr(markets_route, "fetch_earnings_calendar", earnings)
    monkeypatch.setattr(markets_route, "fetch_ipo_calendar", ipos)
    monkeypatch.setattr(markets_route, "fetch_market_holidays", holidays)
    monkeypatch.setattr(markets_route, "fetch_market_status", status)


def test_calendar_route(user: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """The calendar lists FOMC meetings, major earnings, IPOs and upcoming holidays."""
    _serve_calendar(monkeypatch)
    monkeypatch.setattr(markets_route, "finnhub_key", lambda: KEY)

    body = client.get("/api/v1/markets/calendar", params={"days": 60}).json()

    assert body["fomc"] and all(m["end"] >= date.today().isoformat() for m in body["fomc"])
    assert [e["symbol"] for e in body["earnings"]] == ["AAPL"]
    assert [i["name"] for i in body["ipos"]] == ["Big Co"]
    assert [h["name"] for h in body["holidays"]] == ["Soon"]
    assert body["status"] is None
    assert body["notice"] == "Left out: your Finnhub plan does not cover market status."


def test_calendar_without_finnhub_key_has_fomc_only(user: object) -> None:
    """Without a Finnhub key the Fed's meetings still show, with a notice."""
    body = client.get("/api/v1/markets/calendar", params={"days": 60}).json()

    assert body["fomc"] and body["earnings"] == [] and body["ipos"] == []
    assert "add a key" in body["notice"]


def test_market_news_route_needs_a_key(user: object) -> None:
    """Market news without a Finnhub key is a 403."""
    assert client.get("/api/v1/markets/news").status_code == 403
