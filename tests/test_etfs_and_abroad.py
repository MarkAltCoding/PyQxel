"""Tests for ETFs and listings abroad: which providers are asked for each exchange, how a
fund's snapshot gets its kind and price, what a listing no plan covers says, depositary
receipts' profiles, a fund's holdings and costs, and a foreign holding valued in dollars.
Every provider is faked; no test reaches a service."""

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.v1.endpoints import portfolio
from app.core.cache import configure_cache
from app.core.credentials import ProviderKeys, use_provider_keys
from app.data import exchange_rates, fetcher, funds
from app.data.fetcher import (
    ProviderPlanError,
    alpha_vantage_symbol,
    available_providers,
    exchange_name,
    fetch_ticker_info,
    provider_covers,
)
from app.data.funds import parse_etf_profile
from app.main import app
from app.models.stock import TickerInfo

REAL_CLIENT = httpx2.AsyncClient

Handler = Callable[[httpx2.Request], httpx2.Response]


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No cache, nothing remembered, and yfinance off."""
    configure_cache(None)
    fetcher._recalled.clear()
    fetcher._tiingo_meta_memory.clear()
    fetcher._us_listing_memory.clear()
    fetcher.clear_quote_cache()
    monkeypatch.setattr(fetcher, "yfinance_fallback_on", lambda: False)
    yield
    use_provider_keys(ProviderKeys())
    fetcher._recalled.clear()


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


# Which providers are asked for each exchange.


def test_listings_abroad_are_asked_only_of_providers_listing_their_exchange() -> None:
    assert provider_covers("eodhd", "SAP.DE") and provider_covers("eodhd", "VOD.L")
    assert not provider_covers("eodhd", "7203.T") and not provider_covers("eodhd", "0700.HK")
    assert provider_covers("eodhd", "SAP.XETRA")
    assert provider_covers("alpha_vantage", "VOD.L")
    assert not provider_covers("alpha_vantage", "7203.T")
    assert not provider_covers("tiingo", "VOD.L")
    # US listings, ADRs among them, and indices are not limited.
    assert all(provider_covers(p, "ASML") for p in ("eodhd", "alpha_vantage", "tiingo"))
    assert provider_covers("eodhd", "^GSPC")


def test_alpha_vantage_names_listings_abroad_its_own_way() -> None:
    assert alpha_vantage_symbol("VOD.L") == "VOD.LON"
    assert alpha_vantage_symbol("SAP.DE") == "SAP.DEX"
    assert alpha_vantage_symbol("RY.TO") == "RY.TRT"
    assert alpha_vantage_symbol("VOO") == "VOO"
    assert exchange_name("7203.T") == "the Tokyo Stock Exchange"


def test_a_tokyo_listing_skips_the_providers_that_do_not_list_tokyo() -> None:
    use_provider_keys(ProviderKeys(eodhd=SecretStr("eodhd-key"), finnhub=SecretStr("finnhub-key")))

    assert [p for p, _ in available_providers("history", "7203.T")] == ["finnhub"]
    assert [p for p, _ in available_providers("history", "SAP.DE")] == ["eodhd", "finnhub"]


def test_no_key_listing_an_exchange_is_a_plan_error_naming_it() -> None:
    use_provider_keys(ProviderKeys(eodhd=SecretStr("eodhd-key")))

    with pytest.raises(ProviderPlanError, match="the Tokyo Stock Exchange"):
        available_providers("history", "7203.T")


# A fund's snapshot.


def _tiingo_fund(request: httpx2.Request) -> httpx2.Response:
    """Tiingo knowing VOO as an ETF, and Finnhub quoting it."""
    path = request.url.path
    if path.endswith("/tiingo/daily/voo"):
        return httpx2.Response(
            200, json={"ticker": "VOO", "name": "Vanguard 500 ETF", "exchangeCode": "NYSE ARCA"}
        )
    if path.endswith("/tiingo/utilities/search"):
        return httpx2.Response(
            200,
            json=[
                {"ticker": "VOOG", "assetType": "ETF", "countryCode": "US"},
                {"ticker": "VOO", "assetType": "ETF", "countryCode": "US", "name": "Vanguard"},
            ],
        )
    if path.endswith("/quote"):
        return httpx2.Response(200, json={"c": 673.97, "pc": 704.78, "t": 1793912400})
    if path.endswith("/stock/profile2"):
        # Finnhub has no profile for an ETF.
        return httpx2.Response(200, json={})
    return httpx2.Response(404, json={"detail": "not found"})


@pytest.mark.asyncio
async def test_an_etf_from_tiingo_is_typed_and_priced_by_the_cheapest_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(
        ProviderKeys(tiingo=SecretStr("tiingo-key"), finnhub=SecretStr("finnhub-key"))
    )
    requests = _serve(monkeypatch, _tiingo_fund)

    info = await fetch_ticker_info("VOO")

    assert (info.source, info.quote_type, info.price) == ("tiingo", "ETF", 673.97)
    assert info.exchange == "NYSEArca" and info.market_cap is None
    assert info.filled_fields == ["price"]
    # A fund's company meta is not asked for: it has no sector.
    assert not any("fundamentals" in request.url.path for request in requests)


@pytest.mark.asyncio
async def test_a_money_market_fund_from_tiingo_counts_as_cash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(ProviderKeys(tiingo=SecretStr("tiingo-key")))

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/tiingo/daily/spaxx"):
            return httpx2.Response(200, json={"ticker": "SPAXX", "name": "Fidelity MM"})
        if request.url.path.endswith("/tiingo/utilities/search"):
            return httpx2.Response(
                200,
                json=[
                    {
                        "ticker": "SPAXX",
                        "assetType": "Mutual Fund",
                        "name": "FIDELITY GOVERNMENT MONEY MARKET FUND",
                        "countryCode": "US",
                    }
                ],
            )
        return httpx2.Response(404, json={"detail": "not found"})

    _serve(monkeypatch, handler)

    info = await fetch_ticker_info("SPAXX")

    assert info.quote_type == "MONEYMARKET"


# Depositary receipts.


@pytest.mark.asyncio
async def test_a_depositary_receipts_profile_is_in_dollars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(ProviderKeys(finnhub=SecretStr("finnhub-key")))

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/stock/profile2"):
            # Finnhub describes ASML's home listing in Amsterdam, in euros and millions.
            return httpx2.Response(
                200,
                json={
                    "ticker": "ASML.AS",
                    "name": "ASML Holding NV",
                    "currency": "EUR",
                    "exchange": "NYSE EURONEXT - EURONEXT AMSTERDAM",
                    "marketCapitalization": 500_000.0,
                    "finnhubIndustry": "Semiconductors",
                },
            )
        return httpx2.Response(200, json={"c": 1356.65, "pc": 1496.3, "t": 1793912400})

    async def rate(base: str, quote: str = "USD") -> float:
        assert (base, quote) == ("EUR", "USD")
        return 1.1

    monkeypatch.setattr(exchange_rates, "fetch_exchange_rate", rate)
    _serve(monkeypatch, handler)

    info = await fetch_ticker_info("ASML")

    assert (info.currency, info.exchange, info.price) == ("USD", None, 1356.65)
    assert info.market_cap == pytest.approx(500_000e6 * 1.1)


# A listing abroad no plan covers.


@pytest.mark.asyncio
async def test_a_listing_no_plan_covers_names_its_us_listings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider_keys(
        ProviderKeys(finnhub=SecretStr("finnhub-key"), tiingo=SecretStr("tiingo-key"))
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.endswith("/search") and "finnhub" in str(request.url):
            return httpx2.Response(
                200,
                json={"result": [{"symbol": "7203.T", "description": "TOYOTA MOTOR CORP"}]},
            )
        if path.endswith("/tiingo/utilities/search"):
            assert request.url.params["query"] == "toyota motor"
            return httpx2.Response(
                200,
                json=[
                    {"ticker": "TOYM", "name": "Toyota Motor Corporation", "countryCode": "CA"},
                    {"ticker": "TOYOF", "name": "Toyota Motor Corporation", "countryCode": "US"},
                    {"ticker": "TM", "name": "Toyota Motor Corporation", "countryCode": "US"},
                    {"ticker": "TYIDY", "name": "Toyota Industries", "countryCode": "US"},
                ],
            )
        # Finnhub's free plan refuses listings abroad.
        return httpx2.Response(403, json={"error": "You don't have access to this resource."})

    _serve(monkeypatch, handler)

    with pytest.raises(ProviderPlanError) as raised:
        await fetcher.fetch_price_history("7203.T", "1y")

    message = str(raised.value)
    assert "TM and TOYOF" in message and "TYIDY" not in message
    assert await fetcher.us_listings("7203.T") == ["TM", "TOYOF"]


# A fund's holdings and costs.

ETF_PROFILE: dict[str, Any] = {
    "net_assets": "1700000000000",
    "net_expense_ratio": "0.0003",
    "portfolio_turnover": "0.02",
    "dividend_yield": "0.01",
    "inception_date": "2010-09-07",
    "leveraged": "NO",
    "sectors": [
        {"sector": "FINANCIALS", "weight": "0.099"},
        {"sector": "INFORMATION TECHNOLOGY", "weight": "0.396"},
    ],
    "holdings": [
        {"symbol": "AAPL", "description": "APPLE INC", "weight": "0.0738"},
        {"symbol": "NVDA", "description": "NVIDIA CORP", "weight": "0.0839"},
        {"symbol": "n/a", "description": "CASH", "weight": "0.001"},
    ],
}
"""Alpha Vantage's ETF_PROFILE answer, as its free plan gave it for VOO, cut short."""


def test_a_fund_profile_is_read_largest_first() -> None:
    fund = parse_etf_profile("VOO", ETF_PROFILE)

    assert (fund.net_assets, fund.expense_ratio, fund.leveraged) == (1.7e12, 0.0003, False)
    assert [sector.name for sector in fund.sectors] == ["Information Technology", "Financials"]
    assert [holding.symbol for holding in fund.holdings] == ["NVDA", "AAPL", None]
    assert fund.holdings_count == 3 and str(fund.inception_date) == "2010-09-07"


def test_the_fund_route_needs_an_alpha_vantage_key() -> None:
    response = TestClient(app).get("/api/v1/stocks/VOO/fund")

    assert response.status_code == 403
    assert "Alpha Vantage" in response.json()["detail"]


def test_the_fund_route_serves_alpha_vantages_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher.clear_alpha_vantage_memory()
    # Tests sign in without sending provider keys, so the key is handed to the route.
    monkeypatch.setattr(funds, "alpha_vantage_key", lambda: "alpha-vantage-key")

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.params["function"] == "ETF_PROFILE"
        return httpx2.Response(200, text=json.dumps(ETF_PROFILE))

    _serve(monkeypatch, handler)

    response = TestClient(app).get("/api/v1/stocks/VOO/fund")

    assert response.status_code == 200, response.text
    assert response.json()["expense_ratio"] == 0.0003


# A holding abroad, valued in dollars.


def test_a_holding_abroad_is_valued_in_dollars(monkeypatch: pytest.MonkeyPatch) -> None:
    async def info(symbol: str) -> TickerInfo:
        prices = {"VOD.L": (120.0, "GBX"), "SAP.DE": (200.0, "EUR"), "VOO": (600.0, "USD")}
        price, currency = prices[symbol]
        return TickerInfo(symbol=symbol, price=price, currency=currency, source="test")

    async def rate(base: str, quote: str = "USD") -> float:
        return {"GBP": 1.25, "EUR": 1.1}[base]

    monkeypatch.setattr(portfolio, "fetch_ticker_info", info)
    monkeypatch.setattr(exchange_rates, "fetch_exchange_rate", rate)
    client = TestClient(app)
    client.put(
        "/api/v1/portfolio/holdings",
        json={
            "holdings": [
                {"symbol": "VOD.L", "shares": 100},
                {"symbol": "SAP.DE", "shares": 10},
                {"symbol": "VOO", "shares": 1},
            ]
        },
    )

    body = client.get("/api/v1/portfolio/holdings").json()

    held = {h["symbol"]: h for h in body["holdings"]}
    # London quotes pence: 120p is £1.20, or $1.50.
    assert held["VOD.L"]["price"] == pytest.approx(1.5)
    assert (held["VOD.L"]["local_price"], held["VOD.L"]["currency"]) == (120.0, "GBX")
    assert held["SAP.DE"]["value"] == pytest.approx(10 * 200.0 * 1.1)
    assert held["VOO"]["local_price"] is None
    assert body["total_value"] == pytest.approx(150.0 + 2200.0 + 600.0)
