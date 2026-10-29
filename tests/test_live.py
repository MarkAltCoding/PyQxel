"""End-to-end tests against the real services, through the API routes.

Run with ``pytest --live``; the Claude analysis also needs ``--paid``. Tests that need
a key from ``.env`` are skipped when it is not set. Assertions are loose because
market data changes daily: they check that each service answers with plausible data.
"""

import importlib.util
import json
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.credentials import ProviderKeys, use_provider_keys
from app.db.users import User
from app.api.v1.endpoints.research import prepare_analysis
from app.data import fetcher, fomc
from app.data.alpha_vantage import fetch_alpha_vantage_financials
from app.data.exchange_rates import fetch_exchange_rate
from app.data.factors import fetch_factors
from app.data.alternative import fetch_alternative
from app.data.finnhub import fetch_company_metrics, fetch_signals, search_symbols
from app.data.finnhub_financials import fetch_finnhub_financials
from app.data.fomc import FALLBACK_MEETINGS, fetch_fomc_meetings
from app.data.markets import (
    fetch_earnings_calendar,
    fetch_ipo_calendar,
    fetch_market_holidays,
    fetch_market_news,
    fetch_market_status,
)
from app.data.news import fetch_company_news
from app.data.panel import fetch_close_panel
from app.data.sec_edgar import edgar_client, fetch_latest_filings
from app.data.universe import FmpSession, fetch_listings, fetch_profiles
from app.main import app
from app.models.research import AnalysisRequest
from app.stats.monte_carlo import simulate_portfolio
from app.stats.panel import return_panel

pytestmark = [
    pytest.mark.live,
    # TestClient runs startup off the main thread; uvicorn starts R on the main thread.
    pytest.mark.filterwarnings("ignore:R is not initialized by the main thread"),
]

settings = get_settings()
needs_fmp = pytest.mark.skipif(
    settings.financial_data_api_key is None, reason="FINANCIAL_DATA_API_KEY is not set"
)
needs_screener_fmp = pytest.mark.skipif(
    settings.screener_fmp_api_key is None, reason="SCREENER_FMP_API_KEY is not set"
)
needs_sec = pytest.mark.skipif(not settings.sec_user_agent, reason="SEC_USER_AGENT is not set")
needs_alpha_vantage = pytest.mark.skipif(
    settings.alpha_vantage_api_key is None, reason="ALPHA_VANTAGE_API_KEY is not set"
)
needs_finnhub = pytest.mark.skipif(
    settings.finnhub_api_key is None, reason="FINNHUB_API_KEY is not set"
)


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """A client running the app's startup, so R is started as in production."""
    with TestClient(app) as test_client:
        yield test_client


def test_yfinance_quote(client: TestClient) -> None:
    """yfinance returns a priced snapshot for a large listing."""
    response = client.get("/api/v1/stocks/AAPL")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] in {"yfinance", "fmp"}
    assert body["price"] > 0
    assert body["name"]


def test_unknown_symbol_is_404(client: TestClient) -> None:
    """A symbol no provider knows is a 404, not a server error."""
    assert client.get("/api/v1/stocks/ZZZZQQ").status_code == 404


def test_yfinance_history(client: TestClient) -> None:
    """A year of daily bars comes back oldest first with full coverage."""
    response = client.get("/api/v1/stocks/SPY/history", params={"period": "1y"})

    assert response.status_code == 200
    body = response.json()
    bars = body["bars"]
    assert 240 <= len(bars) <= 260
    assert body["coverage"] == "full"
    assert bars[0]["timestamp"] < bars[-1]["timestamp"]
    assert all(bar["close"] > 0 for bar in bars)


@needs_fmp
@pytest.mark.asyncio
async def test_fmp_profile() -> None:
    """The FMP key is accepted and the profile maps onto a snapshot."""
    assert settings.financial_data_api_key is not None
    async with httpx2.AsyncClient(timeout=fetcher.HTTP_TIMEOUT_SECONDS) as http:
        snapshot = await fetcher._fmp_info(
            "AAPL", settings.financial_data_api_key.get_secret_value(), http
        )

    assert snapshot.source == "fmp"
    assert snapshot.name == "Apple Inc."
    assert snapshot.price is not None and snapshot.price > 0


@needs_sec
@pytest.mark.asyncio
async def test_sec_filings() -> None:
    """EDGAR serves the latest 10-K with both sections located."""
    filings = await fetch_latest_filings("AAPL")

    annual = filings[0]
    assert annual.form == "10-K"
    titles = [section.title for section in annual.sections]
    assert titles == ["Item 1A. Risk Factors", "Item 7. Management's Discussion and Analysis"]
    assert all(len(section.text) > 5_000 for section in annual.sections)


@needs_sec
@needs_screener_fmp
@pytest.mark.asyncio
async def test_screener_universe_sources() -> None:
    """The SEC listing and FMP's company screener give tickers, market caps and sectors to
    join."""
    assert settings.screener_fmp_api_key is not None
    async with edgar_client() as sec:
        listings = await fetch_listings(sec)
    async with FmpSession(
        settings.screener_fmp_api_key.get_secret_value(),
        settings.screener_fmp_requests_per_minute,
    ) as session:
        profiles = await fetch_profiles(session)

    symbols = {listing.symbol for listing in listings}
    assert {"AAPL", "BRK-B", "GOOGL"} <= symbols
    assert len(listings) > 4_000
    assert profiles["BRK-B"].market_cap is not None and profiles["BRK-B"].market_cap > 1e11
    assert profiles["AAPL"].sector == "Technology"


@needs_sec
def test_fundamentals(client: TestClient) -> None:
    """EDGAR's company facts give Microsoft's June fiscal years, TTM figures and multiples."""
    response = client.get("/api/v1/stocks/MSFT/fundamentals")

    assert response.status_code == 200
    body = response.json()
    revenue = body["financials"]["revenue"]
    assert revenue["ttm"] > 100e9
    assert all(year["fiscal_year_end"][5:7] == "06" for year in revenue["fiscal_years"])
    assert body["financials"]["total_debt"] is not None
    valuation = body["valuation"]
    assert valuation["market_cap_source"] == "provider"
    assert 5 < valuation["pe_ratio"] < 100
    assert 0 < valuation["operating_margin"] < 1


@pytest.mark.asyncio
async def test_fama_french_factors() -> None:
    """Ken French's library serves every model's factors, recent and in plausible ranges."""
    for model, columns in {
        "ff3": ["Mkt-RF", "SMB", "HML", "RF"],
        "carhart4": ["Mkt-RF", "SMB", "HML", "Mom", "RF"],
        "ff5": ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"],
    }.items():
        frame = await fetch_factors(model)  # type: ignore[arg-type]

        assert list(frame.columns) == columns
        assert len(frame) > 10_000, model
        # The library is regenerated monthly, a month or two behind.
        assert pd.Timestamp.now() - frame.index[-1] < pd.Timedelta(days=120)
        assert frame.abs().max().max() < 0.25
        assert 0 <= frame["RF"].iloc[-1] < 0.001


def test_factor_regression(client: TestClient) -> None:
    """A broad index fund loads about one on the market and the factors explain it."""
    response = client.get("/api/v1/stocks/SPY/factors", params={"model": "carhart4"})

    assert response.status_code == 200
    fit = response.json()["fit"]
    market = fit["exposures"][0]
    assert market["factor"] == "Mkt-RF"
    assert 0.9 < market["estimate"] < 1.1
    assert fit["r_squared"] > 0.95
    assert abs(fit["alpha"]["estimate"]) < 0.05


@pytest.mark.asyncio
async def test_return_panel_across_exchanges() -> None:
    """US and Tokyo listings line up by date, leaving out only each market's holidays."""
    closes = await fetch_close_panel(["SPY", "QQQ", "7203.T"], "2y")

    panel = return_panel(closes.closes)

    assert list(panel.returns.columns) == ["SPY", "QQQ", "7203.T"]
    assert 420 < panel.observations < 510
    assert 0 < panel.excluded_dates < 80
    assert panel.returns["SPY"].corr(panel.returns["QQQ"]) > 0.8
    assert closes.timezones["7203.T"] == "Asia/Tokyo"


def test_copula_fit(client: TestClient) -> None:
    """Two broad US equity funds move almost as one and crash together; bonds barely relate."""
    response = client.post(
        "/api/v1/portfolio/copula", json={"symbols": ["SPY", "QQQ", "TLT"], "period": "5y"}
    )

    assert response.status_code == 200
    body = response.json()
    spy_qqq, spy_tlt = body["pairs"][0], body["pairs"][1]
    assert spy_qqq["kendall_tau"] > 0.6
    assert spy_qqq["tail_dependence"] > 0.3
    assert abs(spy_tlt["kendall_tau"]) < 0.3
    assert body["student_t"]["degrees_of_freedom"] < 30


@pytest.mark.asyncio
async def test_monte_carlo_on_real_returns() -> None:
    """A 60/40 stock-bond mix has a plausible one-month risk under every dependence model."""
    closes = await fetch_close_panel(["SPY", "TLT"], "5y")
    returns = return_panel(closes.closes).returns

    for dependence in ("gaussian", "student_t", "empirical"):
        summary = simulate_portfolio(
            returns,
            {"SPY": 0.6, "TLT": 0.4},
            horizon=21,
            paths=10_000,
            dependence=dependence,
            seed=1,
        )
        var_95 = summary.risk[0].value_at_risk
        assert 0.02 < var_95 < 0.15, (dependence, var_95)
        assert 0.25 < summary.probability_of_loss < 0.5
        assert -0.05 < summary.expected_return < 0.05


def test_portfolio_simulation(client: TestClient) -> None:
    """A real 60/40 portfolio simulates, stores and reads back."""
    response = client.post(
        "/api/v1/portfolio/simulate",
        json={
            "holdings": [{"symbol": "SPY", "weight": 0.6}, {"symbol": "TLT", "weight": 0.4}],
            "horizon": 21,
            "paths": 2_000,
            "seed": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert 0.01 < body["simulation"]["risk"][0]["value_at_risk"] < 0.15
    assert client.get(f"/api/v1/portfolio/simulations/{body['id']}").json() == body


@pytest.mark.asyncio
async def test_analysis_context_has_factor_exposures(user: User) -> None:
    """Real prices and factor data give Claude factor exposures, without calling Claude."""
    use_provider_keys(ProviderKeys(fmp=get_settings().financial_data_api_key))
    prepared = await prepare_analysis("AAPL", user, AnalysisRequest(include_filings=False))

    factors = prepared.context.factors
    assert factors is not None, prepared.context.notice
    assert factors.model == "carhart4"
    market = factors.fit.exposures[0]
    assert market.factor == "Mkt-RF"
    assert 0.3 < market.estimate < 2.0


def test_ewma_volatility(client: TestClient) -> None:
    """EWMA volatility for a broad index fund is in a plausible range."""
    response = client.get("/api/v1/stocks/SPY/volatility", params={"model": "ewma"})

    assert response.status_code == 200
    fit = response.json()["fit"]
    assert 0.03 < fit["current_volatility"] < 1.0


def test_garch_volatility_in_r(client: TestClient) -> None:
    """GARCH(1,1) fits in the real R session and forecasts a plausible volatility."""
    response = client.get("/api/v1/stocks/SPY/volatility", params={"model": "garch", "horizon": 5})

    if response.status_code == 503:
        pytest.skip(f"R is unavailable: {response.json()['detail']}")
    assert response.status_code == 200
    fit = response.json()["fit"]
    assert 0.5 < fit["persistence"] < 1.0
    assert len(fit["forecast"]) == 5
    assert 0.03 < fit["long_run_volatility"] < 1.0


def test_backtest(client: TestClient) -> None:
    """A default backtest on real data scores the strategy and benchmark consistently."""
    response = client.post("/api/v1/stocks/SPY/backtest")

    assert response.status_code == 200
    body = response.json()
    assert len(body["equity_curve"]) > 1_000
    for metrics in (body["metrics"], body["benchmark"]):
        assert -1 < metrics["max_drawdown"] < 0
        assert metrics["sharpe_ratio"] is not None
        assert 0.03 < metrics["annualized_volatility"] < 1.0
    last = body["equity_curve"][-1]
    assert last["benchmark"] == pytest.approx(1 + body["benchmark"]["total_return"])


def test_portfolio_backtest_with_cash_and_attribution(client: TestClient) -> None:
    """Momentum across stocks and bonds, with T-bill interest on cash and factor attribution."""
    response = client.post(
        "/api/v1/portfolio/backtest",
        json={
            "holdings": [{"symbol": "SPY", "weight": 0.6}, {"symbol": "TLT", "weight": 0.4}],
            "strategy": {"type": "time_series_momentum"},
            "cash_interest": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [asset["symbol"] for asset in body["assets"]] == ["SPY", "TLT"]
    fit = body["attribution"]["fit"]
    assert fit["observations"] > 500
    market = next(e for e in fit["exposures"] if e["factor"] == "Mkt-RF")
    assert -0.5 < market["estimate"] < 1.0


@pytest.mark.paid
@needs_sec
def test_claude_analysis_with_filings(client: TestClient) -> None:
    """The full research path: prices, filings and a structured report from Claude.

    Makes one billed request with filings, roughly $0.10-$0.20.
    """
    response = client.post("/api/v1/stocks/AAPL/analysis", json={"kind": "risk"})

    assert response.status_code == 200, response.text
    body = response.json()
    context = body["context"]
    assert [filing["form"] for filing in context["filings"]][:1] == ["10-K"]
    assert body["report"]["risk_level"] in {"low", "moderate", "elevated", "high"}
    assert body["report"]["key_risks"]
    assert body["context"]["factors"]["model"] == "carhart4"


@pytest.mark.asyncio
async def test_quote_from_yfinance() -> None:
    """yfinance quotes a large cap with a price and a move since the previous close."""
    fetcher._quote_cache.clear()
    quote = await fetcher.fetch_quote("AAPL")

    assert quote.source == "yfinance"
    assert quote.price > 0
    assert quote.change_percent is not None and abs(quote.change_percent) < 0.5


def test_quote_websocket(client: TestClient) -> None:
    """The quote socket confirms the subscription and quotes it, dropping unknown symbols."""
    with client.websocket_connect("/api/v1/ws/quotes?symbols=MSFT,ZZZZQQ") as socket:
        messages = [socket.receive_json() for _ in range(4)]

    assert messages[0] == {"type": "subscriptions", "symbols": ["MSFT", "ZZZZQQ"]}
    assert any(m["type"] == "quote" and m["quote"]["symbol"] == "MSFT" for m in messages)
    assert {"type": "subscriptions", "symbols": ["MSFT"]} in messages


@pytest.mark.paid
def test_claude_analysis_stream(client: TestClient) -> None:
    """The streamed thesis sends the context, report fragments and a validated result.

    Makes one billed request without filings, roughly $0.05.
    """
    with client.stream(
        "POST", "/api/v1/stocks/AAPL/analysis/stream", json={"include_filings": False}
    ) as response:
        assert response.status_code == 200
        events = [
            line[len("event: ") :] for line in response.iter_lines() if line.startswith("event: ")
        ]

    assert events[0] == "context"
    assert "report" in events
    assert events[-1] == "result", events[-3:]


@needs_alpha_vantage
@pytest.mark.asyncio
async def test_alpha_vantage_serves_everything_on_its_own_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With only an Alpha Vantage key, free or paid, every kind of data has an answer.

    Uses about nine of a free key's 25 daily requests.
    """
    key = settings.alpha_vantage_api_key
    assert key is not None
    no_fallback = Settings(yfinance_fallback=False)
    monkeypatch.setattr(fetcher, "get_settings", lambda: no_fallback)
    use_provider_keys(ProviderKeys(alpha_vantage=key))

    info = await fetcher.fetch_ticker_info("IBM")
    quote = await fetcher.fetch_quote("IBM")
    history = await fetcher.fetch_price_history("IBM", period="3mo", interval="1d")
    rate = await fetch_exchange_rate("EUR", "USD")
    financials = await fetch_alpha_vantage_financials("IBM", key.get_secret_value())

    # A free key gets no overview, so no sector or market cap; a paid one does.
    assert info.source == "alpha_vantage" and info.name and info.price
    assert quote.source == "alpha_vantage" and quote.price > 0
    assert len(history) > 40 and (history["Close"] > 0).all()
    assert 0.5 < rate < 2.0
    assert financials.revenue is not None and financials.revenue.ttm
    assert financials.total_debt is not None and financials.cash is not None


@needs_finnhub
@pytest.mark.asyncio
async def test_finnhub_free_plan_covers_everything_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With only a Finnhub key, free or paid, quotes, profiles, news, analyst ratings,
    earnings, insider trades and sentiment, key metrics, peers and search all answer for
    a large US stock.

    Uses twelve of the free plan's 60 requests a minute. A part the plan refuses shows up
    in ``signals.notice``.
    """
    key = settings.finnhub_api_key
    assert key is not None
    no_fallback = Settings(yfinance_fallback=False)
    monkeypatch.setattr(fetcher, "get_settings", lambda: no_fallback)
    use_provider_keys(ProviderKeys(finnhub=key))

    quote = await fetcher.fetch_quote("AAPL")
    info = await fetcher.fetch_ticker_info("MSFT")
    news = await fetch_company_news("AAPL")
    signals = await fetch_signals("AAPL", date.today())
    metrics = await fetch_company_metrics("AAPL")
    matches = await search_symbols("apple", 10)

    assert quote.source == "finnhub" and quote.price > 0 and quote.previous_close
    assert info.source == "finnhub" and info.name and info.market_cap and info.price
    assert len(news.articles) > 10
    assert signals.notice is None, signals.notice
    assert signals.recommendations and signals.recommendations[0].total > 10
    assert signals.earnings and signals.earnings[0].actual is not None
    assert signals.insider_window is not None and signals.insider_sentiment
    assert metrics.metrics.pe_ttm and metrics.metrics.week_52_high and metrics.peers
    assert 0 < (metrics.metrics.gross_margin_ttm or 0) < 1
    assert "AAPL" in [match.symbol for match in matches]


@pytest.mark.asyncio
async def test_fed_publishes_fomc_meetings() -> None:
    """The Fed's calendar page still reads, and lists the coming year's meetings."""
    fomc.clear_fomc_memory()
    meetings = await fetch_fomc_meetings()

    assert meetings is not FALLBACK_MEETINGS
    assert sum(meeting.start.year == date.today().year for meeting in meetings) >= 8


@needs_finnhub
@pytest.mark.asyncio
async def test_finnhub_free_plan_covers_market_and_alternative_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Market news, the earnings and IPO calendars, holidays and status, a company's
    lobbying, contracts, patents and filings, and its as-reported financials all answer.

    Uses about 15 requests, more in earnings season, when the calendar is split.
    """
    key = settings.finnhub_api_key
    assert key is not None
    monkeypatch.setattr(fetcher, "get_settings", lambda: Settings(yfinance_fallback=False))
    use_provider_keys(ProviderKeys(finnhub=key))
    today = date.today()

    news = await fetch_market_news("general")
    earnings = await fetch_earnings_calendar(today, today + timedelta(days=13))
    ipos = await fetch_ipo_calendar(today - timedelta(days=30), today + timedelta(days=30))
    holidays = await fetch_market_holidays()
    status = await fetch_market_status()
    records = await fetch_alternative("LMT", today)
    financials = await fetch_finnhub_financials("AAPL")

    assert len(news.articles) > 20
    assert len(earnings) > 100 and len({event.date for event in earnings}) > 5
    assert ipos and holidays and any(holiday.date >= today for holiday in holidays)
    assert status.as_of
    assert not records.missing, records.missing
    assert records.lobbying and records.contracts and records.filings
    assert financials.revenue is not None and financials.revenue.ttm


def _sse_result(
    client: TestClient, path: str, body: dict[str, object]
) -> tuple[list[str], dict[str, Any]]:
    """POST to a Claude stream; return the event names and the result's data."""
    names: list[str] = []
    result: dict[str, Any] = {}
    with client.stream("POST", path, json=body) as response:
        assert response.status_code == 200, response.read()
        event = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
                names.append(event)
            elif line.startswith("data: ") and event in ("result", "error"):
                result = json.loads(line[len("data: ") :])
    assert names[-1] == "result", result
    return names, result


@pytest.mark.paid
def test_claude_portfolio_review_with_a_plan(client: TestClient) -> None:
    """The streamed review of a real portfolio: a schema-valid review whose target
    allocation becomes whole-share orders, tailored to the profile given.

    Makes one billed request, roughly $0.05 to $0.15.
    """
    holdings = [{"symbol": "AAPL", "shares": 20}, {"symbol": "MSFT", "shares": 10}]
    holdings.append({"symbol": "NVDA", "shares": 30})
    assert client.put("/api/v1/portfolio/holdings", json={"holdings": holdings}).status_code == 200
    profile = {
        "risk_tolerance": "moderate",
        "horizon_years": 15,
        "cash_to_invest": 5000,
        "notes": "Taxable account; keep at least some AAPL.",
    }

    names, result = _sse_result(
        client, "/api/v1/portfolio/analysis/stream", {"period": "1y", "profile": profile}
    )

    assert names[0] == "context" and "report" in names
    report, plan = result["report"], result["plan"]
    targets = {t["symbol"].upper(): t["target_weight"] for t in report["target_allocation"]}
    assert 0.95 <= sum(targets.values()) <= 1.05, targets
    assert targets.get("AAPL", 0) > 0, "the notes asked to keep some AAPL"
    assert plan["total_value"] > 5000 and plan["trades"]
    for trade in plan["trades"]:
        assert trade["value"] > 0 and trade["action"] in ("buy", "sell")
        assert trade["shares"] is None or trade["shares"] == int(trade["shares"])
        if trade["order_type"] == "limit" and trade["price"]:
            assert abs(trade["limit_price"] / trade["price"] - 1) < 0.2
    print(json.dumps({"report": report, "plan": plan}, indent=2))


@pytest.mark.paid
def test_claude_coding_assistant_fixes_a_failing_strategy(client: TestClient) -> None:
    """The assistant gets a strategy that fails in the real harness, with its error, and
    returns code that runs there and passes the harness's checks.

    Makes one billed request, roughly $0.03 to $0.10.
    """
    spec = importlib.util.spec_from_file_location(
        "strategy_harness",
        Path(__file__).parents[1] / "web" / "src" / "runners" / "python" / "harness.py",
    )
    assert spec is not None and spec.loader is not None
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    bars = client.get("/api/v1/stocks/SPY/history", params={"period": "2y"}).json()["bars"]
    payload = json.dumps(
        {
            "timestamps": [bar["timestamp"] for bar in bars],
            **{
                key: [bar[key] for bar in bars]
                for key in ("open", "high", "low", "close", "volume")
            },
        }
    )
    code = (
        "def strategy(bars: pd.DataFrame) -> pd.Series:\n"
        "    close = bars['Close']\n"
        "    return (close > close.rolling(50).mean()).astype(float) * close.rolling(20).std()\n"
    )
    failed = json.loads(harness.run_strategy(code, payload))
    assert failed["ok"] is False, "the strategy is meant to fail"

    response = client.post(
        "/api/v1/backtests/assistant",
        json={
            "language": "python",
            "code": code,
            "message": "This fails. Fix it so it is long while above the 50-day average, "
            "sized smaller when volatility is high.",
            "symbol": "SPY",
            "interval": "1d",
            "last_run": {
                "error_kind": failed["error"]["kind"],
                "message": failed["error"]["message"],
                "line": failed["error"]["line"],
                "traceback": failed["error"]["traceback"],
            },
        },
    )

    assert response.status_code == 200, response.text
    reply = response.json()["reply"]
    assert reply["code"], reply["explanation"]
    fixed = json.loads(harness.run_strategy(reply["code"], payload))
    assert fixed["ok"], fixed
    print(f"Error sent: {failed['error']['message']}\n\n{reply['explanation']}\n\n{reply['code']}")
