"""End-to-end tests against the real services, through the API routes.

Run with ``pytest --live``; the Claude analysis also needs ``--paid``. Tests that need
a key from ``.env`` are skipped when it is not set. Assertions are loose because
market data changes daily: they check that each service answers with plausible data.
"""

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.data import fetcher
from app.data.factors import fetch_factors
from app.data.panel import fetch_close_panel
from app.data.sec_edgar import fetch_latest_filings
from app.main import app
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
needs_sec = pytest.mark.skipif(not settings.sec_user_agent, reason="SEC_USER_AGENT is not set")


@pytest.fixture(scope="module")
def client() -> TestClient:
    """A client running the app's startup, so R is started as in production."""
    with TestClient(app) as test_client:
        yield test_client  # type: ignore[misc]


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
    async with httpx.AsyncClient(timeout=fetcher.HTTP_TIMEOUT_SECONDS) as http:
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


def test_ewma_volatility(client: TestClient) -> None:
    """EWMA volatility for a broad index fund is in a plausible range."""
    response = client.get("/api/v1/stocks/SPY/volatility", params={"model": "ewma"})

    assert response.status_code == 200
    fit = response.json()["fit"]
    assert 0.03 < fit["current_volatility"] < 1.0


def test_garch_volatility_in_r(client: TestClient) -> None:
    """GARCH(1,1) fits in the real R session and forecasts a plausible volatility."""
    response = client.get(
        "/api/v1/stocks/SPY/volatility", params={"model": "garch", "horizon": 5}
    )

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
