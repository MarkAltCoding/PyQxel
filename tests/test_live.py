"""End-to-end tests against the real services, through the API routes.

Run with ``pytest --live``; the Claude analysis also needs ``--paid``. Tests that need
a key from ``.env`` are skipped when it is not set. Assertions are loose because
market data changes daily: they check that each service answers with plausible data.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.data import fetcher
from app.data.sec_edgar import fetch_latest_filings
from app.main import app

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
