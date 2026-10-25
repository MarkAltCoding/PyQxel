"""Tests for Finnhub as a market data provider and as the source of analyst ratings,
earnings and insider trades.

Finnhub is reached through a mock transport, so no test touches the network.
"""

from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.v1.endpoints import search as search_route
from app.api.v1.endpoints import signals as signals_route
from app.core.config import Settings
from app.core.credentials import ProviderKeys, use_provider_keys
from app.data import fetcher, finnhub
from app.data.fetcher import (
    FinnhubRateLimitError,
    ProviderNotConfiguredError,
    ProviderPlanError,
    SymbolNotFoundError,
    available_providers,
    fetch_price_history,
    fetch_quote,
    fetch_ticker_info,
    finnhub_free_covers,
    finnhub_get,
)
from app.data.finnhub import fetch_company_metrics, fetch_signals, search_symbols
from app.main import app
from app.models.screener import SearchMatch
from app.models.signals import InsiderTrade, MarketSignals
from app.models.stock import TickerInfo
from app.stats.signals import summarize_insiders, summarize_signals

KEY = "fh-user-key"
TODAY = date(2026, 10, 22)

Answer = Callable[[httpx2.Request], httpx2.Response]

QUOTE = {"c": 150.0, "d": 1.5, "dp": 1.0, "h": 151.0, "l": 148.0, "o": 149.0, "pc": 148.5, "t": 1}
PROFILE = {
    "name": "Apple Inc",
    "currency": "USD",
    "exchange": "NASDAQ NMS - GLOBAL MARKET",
    "finnhubIndustry": "Technology",
    "marketCapitalization": 3_000_000.0,
    "ticker": "AAPL",
}
RECOMMENDATIONS = [
    {"period": "2026-09-01", "strongBuy": 9, "buy": 20, "hold": 6, "sell": 1, "strongSell": 0},
    {"period": "2026-10-01", "strongBuy": 10, "buy": 21, "hold": 5, "sell": 1, "strongSell": 0},
]
EARNINGS = [
    {"period": "2026-06-30", "actual": 1.6, "estimate": 1.5, "surprise": 0.1,
     "surprisePercent": 6.6667, "year": 2026, "quarter": 3},
    {"period": "2026-03-31", "actual": 1.4, "estimate": 1.5, "surprise": -0.1,
     "surprisePercent": -6.6667, "year": 2026, "quarter": 2},
]  # fmt: skip
CALENDAR = {
    "earningsCalendar": [
        {"date": "2026-10-30", "hour": "amc", "epsEstimate": 1.7, "revenueEstimate": 1.2e11,
         "quarter": 4, "year": 2026, "symbol": "AAPL"},
    ]
}  # fmt: skip
INSIDERS = {
    "data": [
        {"name": "Cook Timothy", "change": -50_000, "transactionDate": "2026-10-01",
         "filingDate": "2026-10-03", "transactionCode": "S", "transactionPrice": 150.0},
        {"name": "DOE JANE", "change": 1_000, "transactionDate": "2026-09-15",
         "transactionCode": "P", "transactionPrice": 140.0},
        {"name": "Grant Person", "change": 9_000, "transactionDate": "2026-09-10",
         "transactionCode": "A", "transactionPrice": 0},
        {"name": "Old Seller", "change": -10, "transactionDate": "2026-01-01",
         "transactionCode": "S", "transactionPrice": 100.0},
    ],
    "symbol": "AAPL",
}  # fmt: skip

SENTIMENT = {
    "data": [
        {"symbol": "AAPL", "year": 2026, "month": 8, "change": -5000, "mspr": -40.5},
        {"symbol": "AAPL", "year": 2026, "month": 9, "change": 1200, "mspr": 25.0},
    ],
    "symbol": "AAPL",
}
METRIC = {
    "metric": {
        "52WeekHigh": 348.15, "52WeekHighDate": "2026-10-13", "52WeekLow": 243.42,
        "52WeekLowDate": "2026-01-20", "beta": 1.1, "peTTM": 39.0, "forwardPE": 31.2,
        "grossMarginTTM": 48.65, "dividendYieldIndicatedAnnual": 0.5,
        "marketCapitalization": 5030228.5, "10DayAverageTradingVolume": 39.4,
        "totalDebt/totalEquityQuarterly": 0.78, "evEbitdaTTM": None,
    },
    "metricType": "all",
    "series": {},
    "symbol": "AAPL",
}  # fmt: skip
SEARCH = {
    "count": 3,
    "result": [
        {"description": "APPLE INC", "displaySymbol": "AAPL", "symbol": "AAPL",
         "type": "Common Stock"},
        {"description": "Berkshire Hathaway Inc", "displaySymbol": "BRK.B", "symbol": "BRK.B",
         "type": "Common Stock"},
        {"description": "SPDR S&P 500 ETF Trust", "displaySymbol": "SPY", "symbol": "SPY",
         "type": "ETP"},
    ],
}  # fmt: skip

ROUTES: dict[str, Any] = {
    "stock/insider-sentiment": SENTIMENT,
    "stock/metric": METRIC,
    "stock/peers": ["AAPL", "DELL", "HPQ"],
    "search": SEARCH,
    "quote": QUOTE,
    "stock/profile2": PROFILE,
    "stock/recommendation": RECOMMENDATIONS,
    "stock/earnings": EARNINGS,
    "calendar/earnings": CALENDAR,
    "stock/insider-transactions": INSIDERS,
}


def _finnhub(
    overrides: dict[str, Answer] | None = None,
) -> tuple[httpx2.AsyncClient, list[httpx2.Request]]:
    """A client routed to a fake Finnhub serving :data:`ROUTES`, and the requests it saw."""
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        path = request.url.path.removeprefix("/api/v1/")
        if overrides and path in overrides:
            return overrides[path](request)
        return httpx2.Response(200, json=ROUTES[path])

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), requests


def _paths(requests: list[httpx2.Request]) -> list[str]:
    return [request.url.path.removeprefix("/api/v1/") for request in requests]


@pytest.fixture(autouse=True)
def finnhub_only(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Act as a user with only a Finnhub key, the yfinance fallback off."""
    settings = Settings(yfinance_fallback=False)
    monkeypatch.setattr(fetcher, "get_settings", lambda: settings)
    fetcher.clear_alpha_vantage_memory()
    finnhub.clear_signals_memory()
    use_provider_keys(ProviderKeys(finnhub=SecretStr(KEY)))
    yield
    use_provider_keys(ProviderKeys())
    finnhub.clear_signals_memory()


# The client.


@pytest.mark.asyncio
async def test_key_travels_in_a_header() -> None:
    """The key is sent as a header, never in the URL."""
    client, requests = _finnhub()

    await finnhub_get("quote", {"symbol": "AAPL"}, KEY, client)

    assert requests[0].headers["X-Finnhub-Token"] == KEY
    assert KEY not in str(requests[0].url)


@pytest.mark.asyncio
async def test_plan_refusal_is_remembered_for_the_symbol() -> None:
    """After a 403, the same request is refused at once; other symbols are still asked."""
    client, requests = _finnhub({"quote": lambda request: httpx2.Response(403)})

    for symbol in ("SAP.DE", "SAP.DE", "BMW.DE"):
        with pytest.raises(ProviderPlanError):
            await finnhub_get("quote", {"symbol": symbol}, KEY, client)

    assert [r.url.params["symbol"] for r in requests] == ["SAP.DE", "BMW.DE"]


@pytest.mark.asyncio
async def test_rate_limit_pauses_the_key() -> None:
    """After a 429, Finnhub is not asked again for a minute."""
    client, requests = _finnhub(
        {"quote": lambda request: httpx2.Response(429, headers={"retry-after": "7"})}
    )

    with pytest.raises(FinnhubRateLimitError) as first:
        await finnhub_get("quote", {"symbol": "AAPL"}, KEY, client)
    with pytest.raises(FinnhubRateLimitError):
        await finnhub_get("quote", {"symbol": "MSFT"}, KEY, client)

    assert first.value.retry_after == 7
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_requests_beyond_the_minutes_budget_are_not_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key's per-minute budget is kept, so the free plan's limit is never hit."""
    monkeypatch.setattr(fetcher, "FINNHUB_REQUESTS_PER_MINUTE", 2)
    client, requests = _finnhub()

    await finnhub_get("quote", {"symbol": "AAPL"}, KEY, client)
    await finnhub_get("quote", {"symbol": "MSFT"}, KEY, client)
    with pytest.raises(FinnhubRateLimitError):
        await finnhub_get("quote", {"symbol": "NVDA"}, KEY, client)

    assert len(requests) == 2


@pytest.mark.asyncio
async def test_rejected_key_is_reported() -> None:
    """A 401 means the key is wrong, which the user must fix."""
    client, _ = _finnhub({"quote": lambda request: httpx2.Response(401)})

    with pytest.raises(ProviderNotConfiguredError, match="Finnhub rejected"):
        await finnhub_get("quote", {"symbol": "AAPL"}, KEY, client)


# Quotes and ticker info.


@pytest.mark.parametrize(
    ("symbol", "covered"),
    [("AAPL", True), ("BRK-B", True), ("SAP.DE", False), ("^GSPC", False), ("EURUSD=X", False)],
)
def test_free_plan_covers_us_listings_only(symbol: str, covered: bool) -> None:
    """Indices and listings with an exchange suffix are outside the free plan."""
    assert finnhub_free_covers(symbol) is covered


def test_finnhub_comes_first_for_us_quotes_only() -> None:
    """Finnhub leads US quotes, follows FMP for info, and trails for foreign listings."""
    use_provider_keys(
        ProviderKeys(fmp=SecretStr("fmp"), alpha_vantage=SecretStr("av"), finnhub=SecretStr(KEY))
    )

    def order(kind: Any, symbol: str) -> list[str]:
        return [provider for provider, _ in available_providers(kind, symbol)]

    assert order("quote", "AAPL") == ["finnhub", "fmp", "alpha_vantage"]
    assert order("info", "AAPL") == ["fmp", "finnhub", "alpha_vantage"]
    assert order("quote", "SAP.DE") == ["fmp", "alpha_vantage", "finnhub"]
    assert order("history", "AAPL") == ["fmp", "alpha_vantage"]


@pytest.mark.asyncio
async def test_quote_from_finnhub() -> None:
    """A Finnhub quote has the price, previous close and day's range."""
    client, _ = _finnhub()

    quote = await fetch_quote("aapl", client)

    assert (quote.source, quote.price, quote.previous_close) == ("finnhub", 150.0, 148.5)
    assert quote.change == pytest.approx(1.5)
    assert (quote.day_high, quote.day_low) == (151.0, 148.0)


@pytest.mark.asyncio
async def test_unknown_symbol_quote_is_not_found() -> None:
    """Finnhub's all-zero answer for an unknown symbol is a 404, not a price of zero."""
    zeros = {"c": 0, "d": None, "dp": None, "h": 0, "l": 0, "o": 0, "pc": 0, "t": 0}
    client, _ = _finnhub({"quote": lambda request: httpx2.Response(200, json=zeros)})

    with pytest.raises(SymbolNotFoundError):
        await fetch_quote("ZZZZQ", client)


@pytest.mark.asyncio
async def test_ticker_info_from_finnhub() -> None:
    """With only a Finnhub key, info comes from its profile and quote."""
    client, _ = _finnhub()

    info = await fetch_ticker_info("AAPL", client)

    assert info.source == "finnhub"
    assert (info.name, info.currency, info.industry, info.price) == (
        "Apple Inc",
        "USD",
        "Technology",
        150.0,
    )
    assert info.market_cap == 3e12


@pytest.mark.asyncio
async def test_finnhub_fills_info_fmp_leaves_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fields FMP leaves empty are filled from Finnhub's profile and named."""
    use_provider_keys(ProviderKeys(fmp=SecretStr("fmp"), finnhub=SecretStr(KEY)))

    async def sparse_fmp(symbol: str, api_key: str, client: Any) -> TickerInfo:
        return TickerInfo(symbol=symbol, name="Apple", currency="USD", price=150.0, source="fmp")

    monkeypatch.setattr(fetcher, "_fmp_info", sparse_fmp)
    client, _ = _finnhub()

    info = await fetch_ticker_info("AAPL", client)

    assert info.source == "fmp" and info.name == "Apple"
    assert (info.industry, info.market_cap) == ("Technology", 3e12)
    assert info.filled_fields == ["exchange", "industry", "market_cap"]


@pytest.mark.asyncio
async def test_market_cap_is_not_filled_across_currencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A market cap in another currency than the listing's is left empty."""
    use_provider_keys(ProviderKeys(fmp=SecretStr("fmp"), finnhub=SecretStr(KEY)))

    async def sparse_fmp(symbol: str, api_key: str, client: Any) -> TickerInfo:
        return TickerInfo(symbol=symbol, currency="EUR", source="fmp")

    monkeypatch.setattr(fetcher, "_fmp_info", sparse_fmp)
    client, _ = _finnhub()

    info = await fetch_ticker_info("AAPL", client)

    assert info.market_cap is None and "market_cap" not in info.filled_fields


@pytest.mark.asyncio
async def test_price_history_needs_fmp_or_alpha_vantage() -> None:
    """Finnhub's free plan has no history, so a Finnhub-only user is told which key to add."""
    with pytest.raises(ProviderNotConfiguredError, match="Financial Modeling Prep or Alpha"):
        await fetch_price_history("AAPL", period="1y", interval="1d")


# Signals.


@pytest.mark.asyncio
async def test_signals_read_every_part() -> None:
    """Ratings, earnings, the next report and open-market insider trades are read."""
    client, _ = _finnhub()

    signals = summarize_signals(await fetch_signals("aapl", TODAY, client))

    assert [month.period for month in signals.recommendations] == [
        date(2026, 10, 1),
        date(2026, 9, 1),
    ]
    assert signals.recommendations[0].buy == 21
    assert signals.earnings[0].surprise_percent == pytest.approx(0.066667)
    assert signals.next_earnings is not None
    assert (signals.next_earnings.date, signals.next_earnings.hour) == (date(2026, 10, 30), "amc")
    assert [trade.code for trade in signals.insider_trades] == ["S", "P"]
    assert signals.insider_trades[1].name == "Doe Jane"
    assert signals.insiders is not None
    assert (signals.insiders.purchases, signals.insiders.sales) == (1, 1)
    assert signals.insiders.net_value == pytest.approx(140_000 - 7_500_000)
    assert [(m.month, m.mspr) for m in signals.insider_sentiment] == [
        (date(2026, 9, 1), 25.0),
        (date(2026, 8, 1), -40.5),
    ]
    assert signals.notice is None


@pytest.mark.asyncio
async def test_a_refused_part_is_left_out_with_a_notice() -> None:
    """A part the plan does not cover leaves the others intact."""
    client, _ = _finnhub({"calendar/earnings": lambda request: httpx2.Response(403)})

    signals = await fetch_signals("AAPL", TODAY, client)

    assert signals.next_earnings is None
    assert signals.recommendations and signals.earnings
    assert signals.notice == "Left out: your Finnhub plan does not cover the earnings calendar."


@pytest.mark.asyncio
async def test_signals_are_reused() -> None:
    """A second request is answered from memory."""
    client, requests = _finnhub()

    await fetch_signals("AAPL", TODAY, client)
    await fetch_signals("AAPL", TODAY, client)

    assert len(requests) == 5


@pytest.mark.asyncio
async def test_signals_need_a_key() -> None:
    """Without a Finnhub key nothing is requested."""
    use_provider_keys(ProviderKeys())
    client, requests = _finnhub()

    with pytest.raises(ProviderNotConfiguredError):
        await fetch_signals("AAPL", TODAY, client)

    assert requests == []


def test_insider_summary_counts_people_and_value() -> None:
    """Purchases and sales are totaled, and a trade without a price adds shares only."""
    trades = [
        InsiderTrade(name="A", transaction_date=TODAY, code="P", shares=100, price=10, value=1000),
        InsiderTrade(name="A", transaction_date=TODAY, code="P", shares=50, price=None),
        InsiderTrade(name="B", transaction_date=TODAY, code="S", shares=-40, price=20, value=-800),
    ]

    summary = summarize_insiders(trades, TODAY - timedelta(days=90), TODAY)

    assert (summary.purchases, summary.insiders_buying, summary.sales) == (2, 1, 1)
    assert (summary.shares_bought, summary.shares_sold) == (150, 40)
    assert (summary.value_bought, summary.value_sold, summary.net_value) == (1000, 800, 200)


# Metrics, peers and search.


@pytest.mark.asyncio
async def test_metrics_convert_percents_and_millions() -> None:
    """Ratios become decimals, totals units, and the company is left out of its peers."""
    client, _ = _finnhub()

    result = await fetch_company_metrics("aapl", client)
    metrics = result.metrics

    assert (metrics.week_52_high, metrics.week_52_high_date) == (348.15, date(2026, 10, 13))
    assert (metrics.pe_ttm, metrics.forward_pe, metrics.beta) == (39.0, 31.2, 1.1)
    assert metrics.gross_margin_ttm == pytest.approx(0.4865)
    assert metrics.dividend_yield == pytest.approx(0.005)
    assert metrics.market_cap == pytest.approx(5.0302285e12)
    assert metrics.average_volume_10_day == pytest.approx(39.4e6)
    assert metrics.debt_to_equity == 0.78
    assert metrics.ev_ebitda_ttm is None
    assert result.peers == ["DELL", "HPQ"]


@pytest.mark.asyncio
async def test_unknown_symbol_has_no_metrics() -> None:
    """Finnhub's empty metrics for an unknown symbol are a not-found error."""
    empty = {"metric": {}, "metricType": "all", "series": {}, "symbol": "ZZZZQ"}
    client, _ = _finnhub({"stock/metric": lambda request: httpx2.Response(200, json=empty)})

    with pytest.raises(SymbolNotFoundError):
        await fetch_company_metrics("ZZZZQ", client)


@pytest.mark.asyncio
async def test_metrics_survive_missing_peers() -> None:
    """Peers the plan refuses leave the metrics intact."""
    client, _ = _finnhub({"stock/peers": lambda request: httpx2.Response(403)})

    result = await fetch_company_metrics("AAPL", client)

    assert result.peers == [] and result.metrics.beta == 1.1


@pytest.mark.asyncio
async def test_foreign_listing_metrics_are_a_plan_error() -> None:
    """The free plan refuses metrics for listings outside the US."""
    client, _ = _finnhub({"stock/metric": lambda request: httpx2.Response(403)})

    with pytest.raises(ProviderPlanError):
        await fetch_company_metrics("SAP.DE", client)


@pytest.mark.asyncio
async def test_search_reads_us_listings() -> None:
    """Matches keep their type, take the app's dash for share classes, and read well."""
    client, requests = _finnhub()

    matches = await search_symbols("apple", 10, client)

    assert requests[0].url.params["exchange"] == "US"
    assert [(m.symbol, m.name, m.kind, m.source) for m in matches] == [
        ("AAPL", "Apple Inc", "Common Stock", "finnhub"),
        ("BRK-B", "Berkshire Hathaway Inc", "Common Stock", "finnhub"),
        ("SPY", "SPDR S&P 500 ETF Trust", "ETP", "finnhub"),
    ]


# The routes.

client = TestClient(app)


def test_route_returns_signals(user: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """The route totals insider trades and leaves the fetch window out."""

    async def fake(symbol: str, today: date) -> MarketSignals:
        return MarketSignals(
            symbol=symbol.upper(),
            recommendations=[],
            earnings=[],
            insider_trades=[
                InsiderTrade(name="A", transaction_date=today, code="P", shares=10, value=100)
            ],
            insider_window=(today - timedelta(days=90), today),
        )

    monkeypatch.setattr(signals_route, "fetch_signals", fake)

    body = client.get("/api/v1/stocks/AAPL/signals").json()

    assert body["insiders"]["purchases"] == 1
    assert "insider_window" not in body


def test_route_without_key_is_403(user: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a Finnhub key the route says to add one."""

    async def fake(symbol: str, today: date) -> MarketSignals:
        raise ProviderNotConfiguredError("Add a Finnhub key.")

    monkeypatch.setattr(signals_route, "fetch_signals", fake)

    response = client.get("/api/v1/stocks/AAPL/signals")

    assert response.status_code == 403


def test_metrics_route(user: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """The metrics route serves Finnhub's metrics and maps a refusal to 402."""
    from app.api.v1.endpoints import metrics as metrics_route
    from app.models.metrics import CompanyMetrics, KeyMetrics

    async def fake(symbol: str) -> CompanyMetrics:
        if symbol == "SAP.DE":
            raise ProviderPlanError("Not on your plan.")
        return CompanyMetrics(symbol=symbol, metrics=KeyMetrics(beta=1.2), peers=["MSFT"])

    monkeypatch.setattr(metrics_route, "fetch_company_metrics", fake)

    ok = client.get("/api/v1/stocks/AAPL/metrics")
    refused = client.get("/api/v1/stocks/SAP.DE/metrics")

    assert ok.json()["metrics"]["beta"] == 1.2 and ok.json()["peers"] == ["MSFT"]
    assert refused.status_code == 402


def test_search_adds_finnhub_matches_after_the_universe(
    user: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finnhub fills the list after the universe's matches, without repeating them."""

    async def universe(session: object, query: str, limit: int) -> list[SearchMatch]:
        return [SearchMatch(symbol="AAPL", name="Apple Inc.", exchange="NASDAQ", market_cap=3e12)]

    async def finnhub_search(query: str, limit: int) -> list[SearchMatch]:
        return [
            SearchMatch(symbol="AAPL", name="Apple Inc", source="finnhub"),
            SearchMatch(symbol="APLE", name="Apple Hospitality", kind="REIT", source="finnhub"),
        ]

    monkeypatch.setattr(search_route, "search_stocks", universe)
    monkeypatch.setattr(search_route, "search_symbols", finnhub_search)
    # The test account has no Finnhub key of its own.
    monkeypatch.setattr(search_route, "finnhub_key", lambda: KEY)

    body = client.get("/api/v1/search", params={"q": "apple"}).json()

    assert [(item["symbol"], item["source"]) for item in body["items"]] == [
        ("AAPL", "universe"),
        ("APLE", "finnhub"),
    ]


def test_search_without_finnhub_key_uses_the_universe_only(
    user: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a key, Finnhub is not asked."""

    async def universe(session: object, query: str, limit: int) -> list[SearchMatch]:
        return []

    async def finnhub_search(query: str, limit: int) -> list[SearchMatch]:
        raise AssertionError("Finnhub was asked without a key")

    monkeypatch.setattr(search_route, "search_stocks", universe)
    monkeypatch.setattr(search_route, "search_symbols", finnhub_search)

    assert client.get("/api/v1/search", params={"q": "apple"}).json()["items"] == []
