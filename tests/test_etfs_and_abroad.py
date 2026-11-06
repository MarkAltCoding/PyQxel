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
from app.data.fund_filings import parse_filing
from app.data.funds import parse_etf_profile
from app.stats.fund_stats import HeldCompany, holdings_stats, look_through
from app.models.stock import FundProfile, FundWeight
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


# A fund's SEC filing and the figures weighted from its holdings.

NPORT = b"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo><seriesName>EXAMPLE BALANCED FUND</seriesName><seriesId>S000000001</seriesId>
      <repPdDate>2026-06-30</repPdDate></genInfo>
    <fundInfo><netAssets>1000000.00</netAssets></fundInfo>
    <invstOrSecs>
      <invstOrSec><name>Apple Inc</name><cusip>037833100</cusip><pctVal>40.0</pctVal>
        <assetCat>EC</assetCat><issuerCat>CORP</issuerCat></invstOrSec>
      <invstOrSec><name>Unlisted Co</name><pctVal>10.0</pctVal>
        <assetCat>EC</assetCat><issuerCat>CORP</issuerCat></invstOrSec>
      <invstOrSec><name>United States Treasury Note/Bond</name><pctVal>30.0</pctVal>
        <assetCat>DBT</assetCat><issuerCat>UST</issuerCat>
        <debtSec><maturityDt>2036-06-30</maturityDt><annualizedRt>4.0</annualizedRt></debtSec>
      </invstOrSec>
      <invstOrSec><name>Fannie Mae Pool</name><pctVal>15.0</pctVal>
        <assetCat>ABS-MBS</assetCat><issuerCat>USGSE</issuerCat>
        <debtSec><maturityDt>2031-06-30</maturityDt><annualizedRt>2.0</annualizedRt></debtSec>
      </invstOrSec>
      <invstOrSec><name>Money Market Fund</name><pctVal>5.0</pctVal>
        <assetCat>STIV</assetCat><issuerCat>RF</issuerCat></invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>"""
"""An N-PORT filing cut to the fields read: two stocks, two bonds, and cash."""


def test_a_funds_sec_filing_is_summarized() -> None:
    filing = parse_filing(NPORT)

    assert (filing.series_id, str(filing.as_of), filing.net_assets) == (
        "S000000001",
        "2026-06-30",
        1e6,
    )
    assert filing.asset_mix == pytest.approx({"Stocks": 0.5, "Bonds": 0.45, "Cash": 0.05})
    assert [h.name for h in filing.stocks] == ["Apple Inc", "Unlisted Co"]
    bonds = filing.bonds
    assert bonds is not None and bonds.count == 2
    # Weighted by value: (0.30 x 4% + 0.15 x 2%) / 0.45, and (0.30 x 10 + 0.15 x 5) / 0.45.
    assert bonds.average_coupon == pytest.approx(0.0333, abs=1e-4)
    assert bonds.average_maturity_years == pytest.approx(8.33, abs=0.01)
    assert bonds.issuers == pytest.approx(
        {"US Treasury": 2 / 3, "US agencies and mortgages": 1 / 3}
    )


def test_figures_are_weighted_from_the_holdings() -> None:
    stats = holdings_stats(
        [
            HeldCompany(0.6, {"pe_ratio": 20.0, "net_margin": 0.3, "market_cap": 3e12}, "Tech"),
            HeldCompany(0.3, {"pe_ratio": 40.0, "net_margin": 0.1, "market_cap": 5e9}),
            HeldCompany(0.1, None),
        ]
    )

    assert stats is not None and stats.coverage == pytest.approx(0.9)
    # Harmonic: 0.9 / (0.6 / 20 + 0.3 / 40) = 24, not the arithmetic 26.7.
    assert stats.pe_ratio == pytest.approx(24.0)
    assert stats.net_margin == pytest.approx((0.6 * 0.3 + 0.3 * 0.1) / 0.9)
    assert stats.size_mix == pytest.approx({"large": 2 / 3, "mid": 1 / 3, "small": 0.0})
    assert stats.sectors == {"Tech": 1.0} and stats.sector_coverage == pytest.approx(0.6)
    assert holdings_stats([HeldCompany(0.5, None)]) is None


def test_a_mutual_fund_is_described_from_its_filing_without_any_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def filing(symbol: str) -> Any:
        return parse_filing(NPORT)

    async def names(known: Any) -> list[tuple[str, str]]:
        return [("AAPL", "Apple Inc.")]

    async def figures(symbols: list[str]) -> dict[str, dict[str, float | None]]:
        assert symbols == ["AAPL"]
        return {"AAPL": {"pe_ratio": 30.0, "net_margin": 0.25, "market_cap": 3.5e12}}

    monkeypatch.setattr(funds, "fetch_fund_filing", filing)
    monkeypatch.setattr(funds, "_listed_names", names)
    monkeypatch.setattr(funds, "_finnhub_figures", figures)

    response = TestClient(app).get("/api/v1/stocks/EXBAX/fund")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["source"], body["holdings_source"], body["holdings_as_of"]) == (
        "sec",
        "sec",
        "2026-06-30",
    )
    # Apple is 40 of the fund's 50 points in stocks; the unlisted company has no figures.
    assert body["stats"]["coverage"] == pytest.approx(0.8)
    assert body["stats"]["pe_ratio"] == pytest.approx(30.0)
    assert body["stats_sources"] == ["Finnhub"] and body["stats_as_of"] is not None
    assert body["bonds"]["count"] == 2
    assert any("Alpha Vantage" in note for note in body["notes"])


def test_a_funds_weight_counts_under_what_it_holds() -> None:
    balanced = FundProfile(
        symbol="BAL",
        source="sec",
        asset_mix=[FundWeight(name="Stocks", weight=0.6), FundWeight(name="Bonds", weight=0.4)],
        sectors=[FundWeight(name="Technology", weight=0.5), FundWeight(name="Energy", weight=0.25)],
    )

    split = look_through(0.5, balanced)

    # Of the 0.3 in stocks, three quarters is classified: two thirds of it technology.
    assert split == pytest.approx(
        {"Technology": 0.15, "Energy": 0.075, "Unclassified": 0.075, "Bonds": 0.2}
    )
    assert sum(split.values()) == pytest.approx(0.5)
