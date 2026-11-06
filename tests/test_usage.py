"""Tests for counting each plan's use, the reports Settings shows, estimates of what a page
would use, the user's stored price history, and backtests that ask before running on a
shorter history than asked. Every provider is faked; no test reaches a service."""

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import update

from app.core.cache import configure_cache
from app.core.credentials import ProviderKeys, use_provider_keys
from app.data import fetcher, usage
from app.data.fetcher import (
    HISTORY_NOTICE,
    HISTORY_SHORTFALL,
    HISTORY_STALE,
    fetch_price_history,
)
from app.db.session import get_sessionmaker
from app.db.tables import StoredHistoryRecord
from app.db.users import User
from app.main import app

TIINGO_KEY = "tiingo-user-key"
EODHD_KEY = "eodhd-user-key"
FINNHUB_KEY = "finnhub-user-key"
USER = "user-1"

Handler = Callable[[httpx2.Request], httpx2.Response]
REAL_CLIENT = httpx2.AsyncClient


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No cache, nothing remembered, and yfinance off."""
    configure_cache(None)
    fetcher.clear_alpha_vantage_memory()
    usage.clear_usage_memory()
    monkeypatch.setattr(fetcher, "yfinance_fallback_on", lambda: False)
    yield
    use_provider_keys(ProviderKeys())


def _serve(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> list[httpx2.Request]:
    """Answer every request the fetcher sends with ``handler``; return the requests."""
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        "app.data.fetcher.httpx2.AsyncClient",
        lambda **kwargs: REAL_CLIENT(transport=httpx2.MockTransport(record)),
    )
    return seen


LISTED = date(2005, 1, 3)
"""When the faked listing began."""


def _tiingo_bars(request: httpx2.Request, factor: float = 1.0) -> list[dict[str, Any]]:
    """Tiingo's daily bars from the request's start date, or the listing's, to today."""
    start = max(date.fromisoformat(request.url.params["startDate"]), LISTED)
    days = pd.bdate_range(start=start, end=date.today())
    return [
        {
            "date": f"{day.date().isoformat()}T00:00:00.000Z",
            "adjOpen": factor * (100.0 + (day.date() - LISTED).days / 100),
            "adjHigh": factor * (101.0 + (day.date() - LISTED).days / 100),
            "adjLow": factor * (99.0 + (day.date() - LISTED).days / 100),
            "adjClose": factor * (100.0 + (day.date() - LISTED).days / 100),
            "adjVolume": 1_000.0,
        }
        for day in days
    ]


def _eodhd_year(request: httpx2.Request) -> list[dict[str, Any]]:
    """EODHD's free plan: the last year of bars, whatever was asked."""
    days = pd.bdate_range(start=date.today() - timedelta(days=365), end=date.today())
    return [
        {
            "date": day.date().isoformat(),
            "open": 50.0,
            "high": 51.0,
            "low": 49.0,
            "close": 50.0,
            "adjusted_close": 50.0,
            "volume": 10,
        }
        for day in days
    ]


def _keys(**extra: Any) -> ProviderKeys:
    return ProviderKeys(tiingo=SecretStr(TIINGO_KEY), user_id=USER, **extra)


# Counting and reports.


@pytest.mark.asyncio
async def test_requests_are_counted_per_key_with_their_symbols_and_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(ProviderKeys(tiingo=SecretStr(TIINGO_KEY)))
    _serve(monkeypatch, lambda request: httpx2.Response(200, json=_tiingo_bars(request)))

    await fetch_price_history("MU", "1y")
    await fetch_price_history("GOOG", "1y")
    await fetch_price_history("MU", "2y")

    now = datetime.now(UTC)
    requests, size = await usage.counted(
        "tiingo", TIINGO_KEY, usage.window_start("tiingo", "hour", now)
    )
    assert requests == 3
    assert size > 0
    month = usage.month_of("tiingo", now)
    assert await usage.symbols_counted("tiingo", TIINGO_KEY, month) == {"MU", "GOOG"}
    assert (await usage.counted("tiingo", "another-key-entirely", now - timedelta(days=1)))[0] == 0


@pytest.mark.asyncio
async def test_a_tiingo_report_is_pyqxels_own_count_of_its_free_plan() -> None:
    usage.record("tiingo", TIINGO_KEY, symbols=["MU"], size=2_000)
    usage.record("tiingo", TIINGO_KEY, symbols=["MU", "GOOG"], size=3_000)

    report = await usage.provider_report("tiingo", TIINGO_KEY)

    assert (report.plan, report.source, report.plan_detected) == ("free", "pyqxel", False)
    limits = {(limit.window, limit.unit): limit for limit in report.limits}
    assert (limits["hour", "requests"].used, limits["hour", "requests"].remaining) == (2, 48)
    assert limits["day", "requests"].limit == 1_000
    assert limits["month", "symbols"].used == 2
    assert limits["month", "bytes"].used == 5_000
    assert all(limit.source == "pyqxel" for limit in report.limits)
    assert "only PyQxel's own requests" in (report.note or "")
    day_reset = limits["day", "requests"].resets_at
    assert day_reset is not None and day_reset.astimezone(UTC).hour == 5, "midnight EST"

    power = await usage.provider_report("tiingo", TIINGO_KEY, "power")
    assert {(limit.window, limit.limit) for limit in power.limits} >= {("hour", 10_000)}


@pytest.mark.asyncio
async def test_eodhd_reports_its_own_day_and_minute() -> None:
    today = datetime.now(UTC).date().isoformat()
    usage.record(
        "eodhd",
        EODHD_KEY,
        headers={"X-RateLimit-Limit": "1000", "X-RateLimit-Remaining": "997"},
    )

    report = await usage.provider_report(
        "eodhd",
        EODHD_KEY,
        eodhd_account={
            "apiRequests": 7,
            "apiRequestsDate": today,
            "dailyRateLimit": 20,
            "extraLimit": 500,
        },
    )

    assert (report.source, report.plan_detected, report.plans) == ("provider", True, [])
    limits = {limit.window: limit for limit in report.limits}
    assert (limits["day"].used, limits["day"].remaining, limits["day"].unit) == (7, 13, "calls")
    assert "500 extra calls" in (limits["day"].note or "")
    assert (limits["minute"].used, limits["minute"].remaining) == (3, 997)


@pytest.mark.asyncio
async def test_eodhd_shows_no_calls_today_until_the_first_one() -> None:
    report = await usage.provider_report(
        "eodhd",
        EODHD_KEY,
        eodhd_account={"apiRequests": 20, "apiRequestsDate": "2020-01-01", "dailyRateLimit": 20},
    )

    day = next(limit for limit in report.limits if limit.window == "day")
    assert (day.used, day.remaining) == (0, 20)


def test_finnhub_and_anthropic_headers_are_read() -> None:
    now = datetime.now(UTC)
    reset = int(now.timestamp()) + 30
    finnhub = usage.parse_headers(
        "finnhub",
        {"X-Ratelimit-Limit": "60", "X-Ratelimit-Remaining": "41", "X-Ratelimit-Reset": str(reset)},
        now,
    )
    assert [(r.window, r.limit, r.remaining) for r in finnhub] == [("minute", 60, 41)]
    assert finnhub[0].resets_at == datetime.fromtimestamp(reset, UTC)

    anthropic = usage.parse_headers(
        "anthropic",
        {
            "anthropic-ratelimit-requests-limit": "50",
            "anthropic-ratelimit-requests-remaining": "49",
            "anthropic-ratelimit-requests-reset": "2026-11-05T12:00:01Z",
            "anthropic-ratelimit-input-tokens-limit": "30000",
            "anthropic-ratelimit-input-tokens-remaining": "29000",
        },
        now,
    )
    assert [(r.unit, r.limit, r.remaining) for r in anthropic] == [
        ("requests", 50, 49),
        ("input_tokens", 30_000, 29_000),
    ]
    assert anthropic[0].resets_at == datetime(2026, 11, 5, 12, 0, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_an_anthropic_report_waits_for_its_first_answer() -> None:
    report = await usage.provider_report("anthropic", "sk-ant-key")
    assert [limit.source for limit in report.limits] == ["pyqxel"]
    assert "after your next AI request" in (report.note or "")

    usage.record(
        "anthropic",
        "sk-ant-key",
        headers={
            "anthropic-ratelimit-requests-limit": "50",
            "anthropic-ratelimit-requests-remaining": "48",
        },
    )
    report = await usage.provider_report("anthropic", "sk-ant-key")
    minute = report.limits[0]
    assert (minute.window, minute.used, minute.remaining, minute.source) == (
        "minute",
        2,
        48,
        "provider",
    )


def test_windows_reset_on_each_providers_clock() -> None:
    moment = datetime(2026, 11, 5, 4, 30, tzinfo=UTC)
    assert usage.window_start("tiingo", "day", moment) == datetime(2026, 11, 4, 5, tzinfo=UTC)
    assert usage.window_end("tiingo", "day", moment) == datetime(2026, 11, 5, 5, tzinfo=UTC)
    assert usage.window_end("eodhd", "day", moment) == datetime(2026, 11, 6, tzinfo=UTC)
    assert usage.window_end("tiingo", "month", moment) == datetime(2026, 12, 1, 5, tzinfo=UTC)
    assert usage.window_end("tiingo", "hour", moment) == datetime(2026, 11, 5, 5, tzinfo=UTC)


# The stored price history.


@pytest.mark.asyncio
async def test_one_long_fetch_serves_every_shorter_window(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider_keys(_keys())
    seen = _serve(monkeypatch, lambda request: httpx2.Response(200, json=_tiingo_bars(request)))

    year = await fetch_price_history("MU", "1y")
    assert len(seen) == 1
    asked_from = date.fromisoformat(seen[0].url.params["startDate"])
    assert asked_from <= date.today() - timedelta(days=3650), "ten years, for later reuse"
    assert pd.DatetimeIndex(year.index)[0].date() >= date.today() - timedelta(days=370)

    fetcher._store_memory.clear()  # Read back from the database, as another page would.
    ten = await fetch_price_history("MU", "10y")
    weekly = await fetch_price_history("MU", "5y", "1wk")
    await fetch_price_history("MU", "ytd")

    assert len(seen) == 1, "served from the stored history"
    assert pd.DatetimeIndex(ten.index)[0].date() <= date.today() - timedelta(days=3640)
    assert len(weekly) < len(ten) / 4
    assert HISTORY_NOTICE not in ten.attrs


@pytest.mark.asyncio
async def test_concurrent_pages_share_one_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    use_provider_keys(_keys())
    seen = _serve(monkeypatch, lambda request: httpx2.Response(200, json=_tiingo_bars(request)))

    await asyncio.gather(*(fetch_price_history("MU", period) for period in ("1y", "2y", "5y")))

    assert len(seen) == 1


async def _age_stored(days: int) -> None:
    """Make the stored histories look fetched ``days`` ago."""
    async with get_sessionmaker()() as session:
        await session.execute(
            update(StoredHistoryRecord).values(fetched_at=datetime.now(UTC) - timedelta(days=days))
        )
        await session.commit()
    fetcher._store_memory.clear()


@pytest.mark.asyncio
async def test_a_stale_history_fetches_only_the_days_since_and_follows_a_dividend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(_keys())
    factor = [1.0]
    seen = _serve(
        monkeypatch,
        lambda request: httpx2.Response(200, json=_tiingo_bars(request, factor[0])),
    )
    first = await fetch_price_history("MU", "10y")
    await _age_stored(4)
    factor[0] = 0.99  # A dividend since lowered every adjusted price by 1%.

    again = await fetch_price_history("MU", "10y")

    assert len(seen) == 2
    tail_from = date.fromisoformat(seen[1].url.params["startDate"])
    assert (date.today() - tail_from).days <= 20, "only the days since, and some overlap"
    old_day = pd.DatetimeIndex(first.index)[0]
    assert again["Close"].loc[old_day] == pytest.approx(first["Close"].loc[old_day] * 0.99)
    assert len(again) == len(first)


@pytest.mark.asyncio
async def test_a_spent_key_serves_the_stored_long_copy_not_a_short_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(_keys(eodhd=SecretStr(EODHD_KEY)))
    spent = [False]

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "eodhd.com":
            return httpx2.Response(200, json=_eodhd_year(request))
        if spent[0]:
            return httpx2.Response(429, json={"detail": "You have run over your hourly limit"})
        return httpx2.Response(200, json=_tiingo_bars(request))

    _serve(monkeypatch, handler)
    await fetch_price_history("MU", "10y")
    await _age_stored(2)
    spent[0] = True

    frame = await fetch_price_history("MU", "10y")

    assert pd.DatetimeIndex(frame.index)[0].date() <= date.today() - timedelta(days=3640)
    assert frame.attrs[HISTORY_STALE] is True
    assert "kept from Tiingo" in frame.attrs[HISTORY_NOTICE]
    assert "Tiingo key has used its plan's requests" in frame.attrs[HISTORY_NOTICE]


@pytest.mark.asyncio
async def test_a_short_history_says_which_key_ran_out_and_when(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(_keys(eodhd=SecretStr(EODHD_KEY)))

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "eodhd.com":
            return httpx2.Response(200, json=_eodhd_year(request))
        return httpx2.Response(429, json={"detail": "You have run over your hourly limit"})

    _serve(monkeypatch, handler)

    frame = await fetch_price_history("MU", "10y")

    assert "EODHD's free plan" in frame.attrs[HISTORY_NOTICE]
    [shortfall] = frame.attrs[HISTORY_SHORTFALL]
    assert (shortfall.provider, shortfall.reason) == ("tiingo", "limit")
    assert shortfall.resets_at is not None
    assert shortfall.resets_at - datetime.now(UTC) <= timedelta(hours=1), "the top of the hour"


# Through the API.


@pytest.fixture
def client_with_keys(user: User) -> Iterator[TestClient]:
    """A client whose requests carry free Tiingo and EODHD keys."""
    from app.api.auth import authenticate

    async def signed_in() -> User:
        use_provider_keys(
            ProviderKeys(tiingo=SecretStr(TIINGO_KEY), eodhd=SecretStr(EODHD_KEY), user_id=user.id)
        )
        return user

    app.dependency_overrides[authenticate] = signed_in
    with TestClient(app) as client:
        yield client


def _spent_tiingo(request: httpx2.Request) -> httpx2.Response:
    if request.url.host == "eodhd.com":
        if request.url.path.endswith("/user"):
            return httpx2.Response(
                200, json={"apiRequests": 1, "apiRequestsDate": "2020-01-01", "dailyRateLimit": 20}
            )
        return httpx2.Response(200, json=_eodhd_year(request))
    return httpx2.Response(429, json={"detail": "You have run over your hourly limit"})


def test_a_backtest_asks_before_running_on_a_shorter_history(
    client_with_keys: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, _spent_tiingo)
    body: dict[str, Any] = {
        "strategy": {"type": "buy_and_hold"},
        "period": "10y",
        "attribution": None,
    }

    refused = client_with_keys.post("/api/v1/stocks/MU/backtest", json=body)

    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert "Only part of the 10y window" in detail
    assert "Tiingo key has used its plan's requests" in detail
    assert " ET" in detail and "What to do" in detail

    history = client_with_keys.get("/api/v1/stocks/MU/history?period=10y").json()
    assert history["short_history"] and history["shortfall"][0]["provider"] == "tiingo"

    accepted = client_with_keys.post(
        "/api/v1/stocks/MU/backtest", json=body | {"accept_short_history": True}
    )
    assert accepted.status_code == 200, accepted.text


def test_a_younger_listing_is_not_a_short_history(
    client_with_keys: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, lambda request: httpx2.Response(200, json=_tiingo_bars(request)))
    body = {"strategy": {"type": "buy_and_hold"}, "period": "max", "attribution": None}

    response = client_with_keys.post("/api/v1/stocks/MU/backtest", json=body)

    assert response.status_code == 200, response.text


def test_the_usage_route_reports_every_key_and_the_store(
    client_with_keys: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, _spent_tiingo)
    client_with_keys.get("/api/v1/stocks/MU/history?period=1y")

    response = client_with_keys.get("/api/v1/usage", params={"plan": ["tiingo:power"]})

    assert response.status_code == 200
    data = response.json()
    providers = {p["provider"]: p for p in data["providers"]}
    assert set(providers) == {"tiingo", "eodhd"}
    assert providers["tiingo"]["plan"] == "power"
    assert providers["eodhd"]["plan_detected"] is True
    assert data["stored_prices"]["symbols"] == 1

    assert client_with_keys.delete("/api/v1/usage/stored-prices").status_code == 204
    assert client_with_keys.get("/api/v1/usage").json()["stored_prices"]["symbols"] == 0


def test_an_estimate_counts_stored_histories_as_free(
    client_with_keys: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _serve(
        monkeypatch,
        lambda request: (
            httpx2.Response(200, json={"dailyRateLimit": 20, "apiRequests": 0})
            if request.url.path.endswith("/user")
            else httpx2.Response(200, json=_tiingo_bars(request))
        ),
    )
    client_with_keys.get("/api/v1/stocks/MU/history?period=1y")
    sent = len(seen)
    body = {"fetches": [{"kind": "history", "symbols": ["MU", "GOOG"], "period": "5y"}]}

    estimate = client_with_keys.post("/api/v1/usage/estimate", json=body).json()

    assert estimate["from_store"] == 1
    [tiingo] = estimate["providers"]
    assert (tiingo["provider"], tiingo["requests"], tiingo["new_symbols"]) == ("tiingo", 1, 1)
    assert tiingo["tightest"]["window"] == "hour"
    assert all(request.url.host != "api.tiingo.com" for request in seen[sent:]), "nothing fetched"


@pytest.mark.asyncio
async def test_a_providers_limit_answer_counts_as_its_own_figure() -> None:
    usage.record("fmp", "fmp-user-key")
    now = datetime.now(UTC)

    report = await usage.provider_report(
        "fmp", "fmp-user-key", limit_reached_until=now + timedelta(minutes=30)
    )

    [day] = report.limits
    assert (day.used, day.remaining, day.source) == (250, 0, "provider")
    assert "counting the key's use from anywhere" in (day.note or "")

    hourly = await usage.provider_report(
        "tiingo", TIINGO_KEY, limit_reached_until=usage.window_end("tiingo", "hour", now)
    )
    limits = {limit.window: limit for limit in hourly.limits if limit.unit == "requests"}
    assert (limits["hour"].remaining, limits["hour"].source) == (0, "provider")
    assert limits["day"].source == "pyqxel"


@pytest.mark.asyncio
async def test_tiingo_is_not_asked_for_sectors_its_plan_withholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(ProviderKeys(tiingo=SecretStr(TIINGO_KEY)))
    withheld = "Field not available for free/evaluation"
    seen = _serve(
        monkeypatch,
        lambda request: httpx2.Response(
            200, json=[{"ticker": "x", "name": "X Corp", "sector": withheld, "industry": withheld}]
        ),
    )
    from app.models.stock import TickerInfo

    first = await fetcher._fill_from_tiingo(
        TickerInfo(symbol="MU", name="Micron", source="finnhub"), None
    )
    second = await fetcher._fill_from_tiingo(
        TickerInfo(symbol="GOOG", name="Alphabet", source="finnhub"), None
    )

    assert len(seen) == 1, "the second stock's sector is not asked for"
    assert first.sector is None and second.sector is None


@pytest.mark.asyncio
async def test_a_free_alpha_vantage_key_spends_nothing_on_profiles_others_can_give(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(
        ProviderKeys(alpha_vantage=SecretStr("av-user-key"), finnhub=SecretStr(FINNHUB_KEY))
    )
    seen = _serve(
        monkeypatch,
        lambda request: httpx2.Response(
            200, json={"Information": "This is a premium endpoint. Subscribe to premium plans."}
        ),
    )

    with pytest.raises(fetcher.ProviderPlanError, match="left to your other keys"):
        await fetcher._alpha_vantage_info("MU", "av-user-key", None)

    assert [request.url.params["function"] for request in seen] == ["OVERVIEW"]
