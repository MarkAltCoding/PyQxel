"""Tests for the FastAPI application's routes.

Market data fetchers are replaced with fakes so no test touches the network.
"""

import math
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.ai.agent import (
    AINotConfiguredError,
    AIRateLimitError,
    AIRefusalError,
    AIUnavailableError,
    AnalysisError,
)
from app.api.v1.endpoints import backtest, research, stocks
from app.data.fetcher import DataFetchError, SymbolNotFoundError
from app.data.sec_edgar import CompanyNotFoundError, EdgarNotConfiguredError, FilingFetchError
from app.main import __version__, app
from app.models.research import (
    AnalysisContext,
    Filing,
    FilingSection,
    InvestmentThesis,
    RiskSummary,
)
from app.models.stock import TickerInfo
from app.models.volatility import EwmaFit, GarchFit, GarchParameter, VolatilityForecastStep
from app.stats.garch import ModelFitError
from app.stats.r_bridge import RUnavailableError
from app.stats.volatility import InsufficientDataError

client = TestClient(app)


def test_health_returns_ok() -> None:
    """The health check reports status, app name, and version."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "PyQxel", "version": __version__}


def test_ticker_info_returns_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ticker route returns the fetcher's snapshot unchanged."""
    snapshot = TickerInfo(symbol="AAPL", name="Apple Inc.", price=190.5, source="yfinance")

    async def fake_fetch(symbol: str) -> TickerInfo:
        assert symbol == "aapl"
        return snapshot

    monkeypatch.setattr(stocks, "fetch_ticker_info", fake_fetch)
    response = client.get("/api/v1/stocks/aapl")

    assert response.status_code == 200
    assert response.json() == snapshot.model_dump()


def test_ticker_info_maps_fetch_error_to_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider failures surface as 502 Bad Gateway with the error message."""

    async def failing_fetch(symbol: str) -> TickerInfo:
        raise DataFetchError("Could not fetch info for 'ZZZZ'.")

    monkeypatch.setattr(stocks, "fetch_ticker_info", failing_fetch)
    response = client.get("/api/v1/stocks/ZZZZ")

    assert response.status_code == 502
    assert response.json() == {"detail": "Could not fetch info for 'ZZZZ'."}


def test_ticker_info_maps_unknown_symbol_to_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """A symbol no provider recognizes surfaces as 404 Not Found."""

    async def missing_fetch(symbol: str) -> TickerInfo:
        raise SymbolNotFoundError("Unknown ticker symbol 'ZZZZ'.")

    monkeypatch.setattr(stocks, "fetch_ticker_info", missing_fetch)
    response = client.get("/api/v1/stocks/ZZZZ")

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown ticker symbol 'ZZZZ'."}


@pytest.mark.parametrize("symbol", ["BAD$SYM", "TOOLONGSYMBOL12345"])
def test_ticker_info_rejects_invalid_symbol(symbol: str) -> None:
    """Symbols with disallowed characters or excessive length are rejected before fetching."""
    response = client.get(f"/api/v1/stocks/{symbol}")

    assert response.status_code == 422


def test_price_history_returns_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    """History is serialized oldest first, with missing non-close values as null."""
    index = pd.DatetimeIndex(["2026-09-24", "2026-09-25"], tz="America/New_York")
    frame = pd.DataFrame(
        {
            "Open": [100.0, math.nan],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.5],
            "Close": [101.0, 102.5],
            "Volume": [1_000_000, 1_200_000],
        },
        index=index,
    )
    calls: list[tuple[str, str, str]] = []

    async def fake_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        calls.append((symbol, period, interval))
        return frame

    monkeypatch.setattr(stocks, "fetch_price_history", fake_history)
    response = client.get("/api/v1/stocks/msft/history", params={"period": "5d", "interval": "1d"})

    assert response.status_code == 200
    assert calls == [("msft", "5d", "1d")]
    body = response.json()
    assert body["symbol"] == "MSFT"
    assert body["period"] == "5d"
    assert body["interval"] == "1d"
    assert [bar["close"] for bar in body["bars"]] == [101.0, 102.5]
    assert body["bars"][1]["open"] is None
    assert body["bars"][0]["volume"] == 1_000_000
    assert body["bars"][0]["timestamp"].startswith("2026-09-24T00:00:00")
    assert body["coverage"] == "full"
    assert body["notice"] is None


def test_price_history_uses_default_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omitted query parameters default to one year of daily bars."""
    calls: list[tuple[str, str]] = []

    async def fake_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        calls.append((period, interval))
        return pd.DataFrame(
            {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [0]},
            index=pd.DatetimeIndex(["2026-09-25"]),
        )

    monkeypatch.setattr(stocks, "fetch_price_history", fake_history)
    response = client.get("/api/v1/stocks/SPY/history")

    assert response.status_code == 200
    assert calls == [("1y", "1d")]


def _daily_frame(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Build a business-day OHLCV frame from ``start`` to ``end`` with constant prices."""
    index = pd.bdate_range(start.normalize(), end.normalize())
    return pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 0}, index=index
    )


def _serve_history(monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame) -> None:
    """Make the history route return ``frame`` for any request."""

    async def fake_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        return frame

    monkeypatch.setattr(stocks, "fetch_price_history", fake_history)


def test_price_history_full_window_has_no_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bars starting at the window start report full coverage."""
    now = pd.Timestamp.now()
    _serve_history(monkeypatch, _daily_frame(now - pd.DateOffset(years=1), now))

    body = client.get("/api/v1/stocks/SPY/history", params={"period": "1y"}).json()

    assert body["coverage"] == "full"
    assert body["notice"] is None


def test_price_history_partial_window_shows_available_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recent listing returns every bar it has, with a notice about the missing span."""
    now = pd.Timestamp.now()
    listed = now - pd.DateOffset(months=2)
    frame = _daily_frame(listed, now)
    _serve_history(monkeypatch, frame)

    response = client.get("/api/v1/stocks/NEWCO/history", params={"period": "1y"})

    assert response.status_code == 200
    body = response.json()
    assert body["coverage"] == "partial"
    assert len(body["bars"]) == len(frame)
    assert f"before {frame.index[0].date()}" in body["notice"]


def test_price_history_empty_window_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real symbol with no bars in the window returns 200, no bars, and a notice."""
    empty = pd.DataFrame(
        columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex([])
    )
    _serve_history(monkeypatch, empty)

    response = client.get("/api/v1/stocks/AAPL/history", params={"period": "5d", "interval": "1m"})

    assert response.status_code == 200
    body = response.json()
    assert body["bars"] == []
    assert body["coverage"] == "none"
    assert body["notice"].startswith("No price data exists for AAPL")


@pytest.mark.parametrize("period", ["1d", "5d", "max"])
def test_price_history_open_ended_periods_report_full(
    monkeypatch: pytest.MonkeyPatch, period: str
) -> None:
    """Trading-day and open-ended periods are never flagged as partial."""
    now = pd.Timestamp.now()
    _serve_history(monkeypatch, _daily_frame(now - pd.DateOffset(months=2), now))

    body = client.get("/api/v1/stocks/NEWCO/history", params={"period": period}).json()

    assert body["coverage"] == "full"


def test_price_history_rejects_unknown_period() -> None:
    """Periods outside the yfinance vocabulary fail validation."""
    response = client.get("/api/v1/stocks/AAPL/history", params={"period": "7y"})

    assert response.status_code == 422


def test_price_history_maps_fetch_error_to_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """History provider failures surface as 502 Bad Gateway."""

    async def failing_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        raise DataFetchError("No price history returned for 'AAPL'.")

    monkeypatch.setattr(stocks, "fetch_price_history", failing_history)
    response = client.get("/api/v1/stocks/AAPL/history")

    assert response.status_code == 502


def test_price_history_maps_unknown_symbol_to_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty history for the requested window surfaces as 404 Not Found."""

    async def missing_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        raise SymbolNotFoundError("No price history found for 'ZZZZ' over '1y'.")

    monkeypatch.setattr(stocks, "fetch_price_history", missing_history)
    response = client.get("/api/v1/stocks/ZZZZ/history")

    assert response.status_code == 404


def _garch_fit(distribution: str = "std") -> GarchFit:
    """Build a small fitted model like ``fit_garch`` returns."""
    return GarchFit(
        distribution=distribution,
        observations=500,
        parameters=[GarchParameter(name="beta1", estimate=0.9, std_error=0.02)],
        persistence=0.97,
        half_life=22.8,
        current_volatility=0.3,
        long_run_volatility=0.25,
        realized_volatility=0.27,
        conditional_volatility=[],
        forecast=[VolatilityForecastStep(step=1, volatility=0.29)],
        log_likelihood=-900.0,
        aic=3.6,
        bic=3.7,
    )


def _serve_volatility(
    monkeypatch: pytest.MonkeyPatch, fit: GarchFit | Exception
) -> list[tuple[pd.Series, int, int, str]]:
    """Serve five years of daily bars and make the GARCH fit return or raise ``fit``."""
    now = pd.Timestamp.now()
    _serve_history(monkeypatch, _daily_frame(now - pd.DateOffset(years=5), now))
    calls: list[tuple[pd.Series, int, int, str]] = []

    async def fake_fit(
        closes: pd.Series, periods_per_year: int, horizon: int, distribution: str
    ) -> GarchFit:
        calls.append((closes, periods_per_year, horizon, distribution))
        if isinstance(fit, Exception):
            raise fit
        return fit

    monkeypatch.setattr(stocks, "fit_garch", fake_fit)
    return calls


def test_volatility_returns_garch_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The volatility route fits close prices and wraps the fit with its window."""
    fit = _garch_fit("norm")
    calls = _serve_volatility(monkeypatch, fit)

    response = client.get(
        "/api/v1/stocks/spy/volatility",
        params={"interval": "1wk", "horizon": 5, "distribution": "norm"},
    )

    assert response.status_code == 200
    closes, periods_per_year, horizon, distribution = calls[0]
    assert closes.name == "Close"
    assert (periods_per_year, horizon, distribution) == (52, 5, "norm")
    body = response.json()
    assert body["symbol"] == "SPY"
    assert body["period"] == "5y"
    assert body["interval"] == "1wk"
    assert body["periods_per_year"] == 52
    assert body["coverage"] == "full"
    assert body["fit"] == fit.model_dump(mode="json")


def test_volatility_uses_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omitted parameters fit five years of daily bars with Student t errors."""
    calls = _serve_volatility(monkeypatch, _garch_fit())

    body = client.get("/api/v1/stocks/SPY/volatility").json()

    assert calls[0][1:] == (252, 10, "std")
    assert body["period"] == "5y"
    assert body["interval"] == "1d"
    assert body["fit"]["model"] == "garch"


@pytest.mark.parametrize(
    "params",
    [
        {"interval": "1h"},
        {"interval": "1mo"},
        {"period": "3mo"},
        {"horizon": 0},
        {"distribution": "ged"},
        {"model": "arch"},
        {"decay": 1.0},
        {"decay": 0.4},
    ],
)
def test_volatility_rejects_invalid_parameters(params: dict[str, str | int | float]) -> None:
    """Unsupported bars, periods, horizons, distributions, models and decays fail."""
    response = client.get("/api/v1/stocks/SPY/volatility", params=params)

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (InsufficientDataError("GARCH needs at least 480 returns"), 422),
        (ModelFitError("GARCH fit failed"), 422),
        (RUnavailableError("R is unavailable"), 503),
    ],
)
def test_volatility_maps_model_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception, status_code: int
) -> None:
    """Unfittable data surfaces as 422 and a missing R installation as 503."""
    _serve_volatility(monkeypatch, error)

    response = client.get("/api/v1/stocks/SPY/volatility")

    assert response.status_code == status_code
    assert response.json() == {"detail": str(error)}


def test_volatility_maps_unknown_symbol_to_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown symbol surfaces as 404 without fitting a model."""

    async def missing_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        raise SymbolNotFoundError("Unknown ticker symbol 'ZZZZ'.")

    monkeypatch.setattr(stocks, "fetch_price_history", missing_history)
    response = client.get("/api/v1/stocks/ZZZZ/volatility")

    assert response.status_code == 404


def _ewma_fit(decay: float = 0.94) -> EwmaFit:
    """Build a small estimate like ``fit_ewma`` returns."""
    return EwmaFit(
        decay=decay,
        observations=120,
        half_life=11.2,
        current_volatility=0.6,
        realized_volatility=0.55,
        conditional_volatility=[],
        forecast=[VolatilityForecastStep(step=1, volatility=0.61)],
    )


def test_volatility_ewma_uses_decay(monkeypatch: pytest.MonkeyPatch) -> None:
    """``model=ewma`` estimates EWMA with the requested decay and never calls GARCH."""
    _serve_volatility(monkeypatch, AssertionError("GARCH must not be called"))
    fit = _ewma_fit(0.97)
    calls: list[tuple[int, int, float]] = []

    def fake_ewma(closes: pd.Series, periods_per_year: int, horizon: int, decay: float) -> EwmaFit:
        calls.append((periods_per_year, horizon, decay))
        return fit

    monkeypatch.setattr(stocks, "fit_ewma", fake_ewma)
    response = client.get(
        "/api/v1/stocks/NEWCO/volatility",
        params={"model": "ewma", "period": "6mo", "decay": 0.97, "horizon": 3},
    )

    assert response.status_code == 200
    assert calls == [(252, 3, 0.97)]
    body = response.json()
    assert body["period"] == "6mo"
    assert body["fit"] == fit.model_dump(mode="json")
    assert body["fit"]["model"] == "ewma"


@pytest.mark.parametrize(("interval", "expected"), [("1d", 0.94), ("1wk", 0.97)])
def test_volatility_ewma_default_decay_depends_on_interval(
    monkeypatch: pytest.MonkeyPatch, interval: str, expected: float
) -> None:
    """EWMA without a decay uses 0.94 for daily bars and 0.97 for weekly bars."""
    _serve_volatility(monkeypatch, _garch_fit())
    decays: list[float] = []

    def fake_ewma(closes: pd.Series, periods_per_year: int, horizon: int, decay: float) -> EwmaFit:
        decays.append(decay)
        return _ewma_fit(decay)

    monkeypatch.setattr(stocks, "fit_ewma", fake_ewma)
    response = client.get(
        "/api/v1/stocks/SPY/volatility", params={"model": "ewma", "interval": interval}
    )

    assert response.status_code == 200
    assert decays == [expected]
    assert response.json()["fit"]["decay"] == expected


def test_volatility_ewma_explicit_decay_overrides_weekly_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A requested decay is used as-is, even for weekly bars."""
    _serve_volatility(monkeypatch, _garch_fit())
    decays: list[float] = []

    def fake_ewma(closes: pd.Series, periods_per_year: int, horizon: int, decay: float) -> EwmaFit:
        decays.append(decay)
        return _ewma_fit(decay)

    monkeypatch.setattr(stocks, "fit_ewma", fake_ewma)
    response = client.get(
        "/api/v1/stocks/SPY/volatility",
        params={"model": "ewma", "interval": "1wk", "decay": 0.9},
    )

    assert response.status_code == 200
    assert decays == [0.9]


def test_volatility_ewma_maps_short_history_to_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """Too few returns for EWMA surface as 422."""
    _serve_volatility(monkeypatch, _garch_fit())

    def short_ewma(closes: pd.Series, periods_per_year: int, horizon: int, decay: float) -> EwmaFit:
        raise InsufficientDataError("EWMA needs at least 30 returns")

    monkeypatch.setattr(stocks, "fit_ewma", short_ewma)
    response = client.get("/api/v1/stocks/SPY/volatility", params={"model": "ewma"})

    assert response.status_code == 422
    assert response.json() == {"detail": "EWMA needs at least 30 returns"}


AnalysisCall = tuple[AnalysisContext, str, list[Filing]]


def _serve_analysis(
    monkeypatch: pytest.MonkeyPatch,
    info: TickerInfo | Exception,
    frame: pd.DataFrame | Exception,
    report: InvestmentThesis | RiskSummary | Exception,
    filings: list[Filing] | Exception | None = None,
) -> list[AnalysisCall]:
    """Fake the fetchers and the research agent; return the agent's recorded calls.

    The EDGAR fetch returns ``filings``, or no filings when omitted.
    """
    calls: list[AnalysisCall] = []

    async def fake_info(symbol: str) -> TickerInfo:
        if isinstance(info, Exception):
            raise info
        return info

    async def fake_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        assert interval == "1d"
        if isinstance(frame, Exception):
            raise frame
        return frame

    async def fake_filings(symbol: str) -> list[Filing]:
        if isinstance(filings, Exception):
            raise filings
        return filings or []

    async def fake_write(
        context: AnalysisContext, kind: str, filings: list[Filing]
    ) -> tuple[InvestmentThesis | RiskSummary, str]:
        calls.append((context, kind, filings))
        if isinstance(report, Exception):
            raise report
        return report, "claude-opus-5-5"

    monkeypatch.setattr(research, "fetch_ticker_info", fake_info)
    monkeypatch.setattr(research, "fetch_price_history", fake_history)
    monkeypatch.setattr(research, "fetch_latest_filings", fake_filings)
    monkeypatch.setattr(research, "write_analysis", fake_write)
    return calls


def _filing(form: str = "10-K", truncated: bool = False, sections: bool = True) -> Filing:
    """Build a filing with a Risk Factors section, or none."""
    return Filing(
        form=form,  # type: ignore[arg-type]
        accession_number="0000320193-24-000123",
        filed=date(2024, 11, 1),
        period_of_report=date(2024, 9, 28),
        url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl.htm",
        sections=[
            FilingSection(title="Item 1A. Risk Factors", text="Supply risk.", truncated=truncated)
        ]
        if sections
        else [],
    )


def _recent_frame() -> pd.DataFrame:
    """Daily bars covering the last year, rising steadily."""
    now = pd.Timestamp.now()
    frame = _daily_frame(now - pd.DateOffset(years=1), now)
    frame["Close"] = [100.0 + i + (i % 3) for i in range(len(frame))]
    return frame


THESIS = InvestmentThesis(
    headline="Steady uptrend.",
    stance="bullish",
    conviction="low",
    summary="Prices rose.",
    supporting_points=["Positive return."],
    counterpoints=["Short sample."],
    what_would_change_the_view=["A reversal."],
    data_limitations=["No fundamentals."],
)


def test_analysis_returns_report_and_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """The report is returned with the data it was written from."""
    info = TickerInfo(symbol="AAPL", name="Apple Inc.", source="yfinance")
    calls = _serve_analysis(monkeypatch, info, _recent_frame(), THESIS)

    response = client.post("/api/v1/stocks/aapl/analysis", json={"kind": "thesis"})

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "AAPL"
    assert body["kind"] == "thesis"
    assert body["model"] == "claude-opus-5-5"
    assert body["report"] == THESIS.model_dump()
    assert body["context"]["ticker"]["name"] == "Apple Inc."
    assert body["context"]["coverage"] == "full"
    assert body["context"]["prices"]["period_return"] > 0
    context, kind, _ = calls[0]
    assert kind == "thesis"
    assert context.period == "1y"


def test_analysis_defaults_without_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no body, a one-year thesis is written."""
    info = TickerInfo(symbol="AAPL", source="yfinance")
    calls = _serve_analysis(monkeypatch, info, _recent_frame(), THESIS)

    response = client.post("/api/v1/stocks/AAPL/analysis")

    assert response.status_code == 200
    assert calls[0][0].period == "1y"
    assert calls[0][1] == "thesis"


def test_analysis_rejects_unknown_kind() -> None:
    """Unknown report kinds and periods fail request validation."""
    assert client.post("/api/v1/stocks/AAPL/analysis", json={"kind": "memo"}).status_code == 422
    assert client.post("/api/v1/stocks/AAPL/analysis", json={"period": "1d"}).status_code == 422


def test_analysis_without_snapshot_uses_prices_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed snapshot fetch degrades to a price-only analysis with a notice."""
    calls = _serve_analysis(monkeypatch, DataFetchError("down"), _recent_frame(), THESIS)

    response = client.post("/api/v1/stocks/AAPL/analysis", json={"kind": "thesis"})

    assert response.status_code == 200
    context = calls[0][0]
    assert context.ticker.source == "unavailable"
    assert context.notice is not None and "snapshot" in context.notice


def test_analysis_maps_unknown_symbol_to_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown symbols are 404s and never reach the model."""
    calls = _serve_analysis(
        monkeypatch,
        SymbolNotFoundError("Unknown ticker symbol 'ZZZZ'."),
        SymbolNotFoundError("Unknown ticker symbol 'ZZZZ'."),
        THESIS,
    )

    response = client.post("/api/v1/stocks/ZZZZ/analysis")

    assert response.status_code == 404
    assert calls == []


def test_analysis_maps_short_history_to_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows too short to summarize are 422s and never reach the model."""
    now = pd.Timestamp.now()
    frame = _daily_frame(now - pd.Timedelta(days=10), now)
    calls = _serve_analysis(monkeypatch, TickerInfo(symbol="NEW", source="yfinance"), frame, THESIS)

    response = client.post("/api/v1/stocks/NEW/analysis")

    assert response.status_code == 422
    assert calls == []


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (AINotConfiguredError("no key"), 503),
        (AIRefusalError("declined"), 422),
        (AIUnavailableError("down"), 502),
        (AnalysisError("truncated"), 502),
    ],
)
def test_analysis_maps_ai_errors(
    monkeypatch: pytest.MonkeyPatch, error: AnalysisError, status_code: int
) -> None:
    """AI failures map to status codes with their message as detail."""
    info = TickerInfo(symbol="AAPL", source="yfinance")
    _serve_analysis(monkeypatch, info, _recent_frame(), error)

    response = client.post("/api/v1/stocks/AAPL/analysis")

    assert response.status_code == status_code
    assert response.json() == {"detail": str(error)}


def test_analysis_rate_limit_sets_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rate limits are 503s that pass on the retry hint."""
    info = TickerInfo(symbol="AAPL", source="yfinance")
    _serve_analysis(monkeypatch, info, _recent_frame(), AIRateLimitError("slow down", 12))

    response = client.post("/api/v1/stocks/AAPL/analysis")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "12"


def test_analysis_passes_filings_to_the_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Filing text goes to the agent; the response lists the filings without their text."""
    info = TickerInfo(symbol="AAPL", source="yfinance")
    filing = _filing()
    calls = _serve_analysis(monkeypatch, info, _recent_frame(), THESIS, [filing])

    response = client.post("/api/v1/stocks/AAPL/analysis")

    assert response.status_code == 200
    context, _, filings = calls[0]
    assert filings == [filing]
    assert context.notice is None
    listed = response.json()["context"]["filings"]
    assert listed == [
        {
            "form": "10-K",
            "accession_number": filing.accession_number,
            "filed": "2024-11-01",
            "period_of_report": "2024-09-28",
            "url": filing.url,
            "sections": ["Item 1A. Risk Factors"],
            "truncated_sections": [],
        }
    ]
    assert "Supply risk." not in response.text


def test_analysis_notes_truncated_and_unreadable_filings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cut sections are noted, and filings without sections are noted and dropped."""
    info = TickerInfo(symbol="AAPL", source="yfinance")
    filings = [_filing(truncated=True), _filing(form="10-Q", sections=False)]
    calls = _serve_analysis(monkeypatch, info, _recent_frame(), THESIS, filings)

    response = client.post("/api/v1/stocks/AAPL/analysis")

    assert response.status_code == 200
    context, _, passed = calls[0]
    assert passed == [filings[0]]
    assert context.filings[0].truncated_sections == ["Item 1A. Risk Factors"]
    assert context.notice is not None
    assert "cut short" in context.notice
    assert "10-Q filed 2024-11-01 could not be located" in context.notice


@pytest.mark.parametrize(
    ("error", "notice"),
    [
        (EdgarNotConfiguredError("unset"), "not configured"),
        (CompanyNotFoundError("none"), "AAPL has no SEC filings"),
        (FilingFetchError("down"), "could not be fetched"),
    ],
)
def test_analysis_without_filings_degrades_with_notice(
    monkeypatch: pytest.MonkeyPatch, error: Exception, notice: str
) -> None:
    """EDGAR failures never fail the request; the report is written from prices."""
    info = TickerInfo(symbol="AAPL", source="yfinance")
    calls = _serve_analysis(monkeypatch, info, _recent_frame(), THESIS, error)

    response = client.post("/api/v1/stocks/AAPL/analysis")

    assert response.status_code == 200
    context, _, filings = calls[0]
    assert filings == []
    assert context.filings == []
    assert context.notice is not None and notice in context.notice


def test_analysis_can_skip_filings(monkeypatch: pytest.MonkeyPatch) -> None:
    """``include_filings: false`` never contacts EDGAR."""
    info = TickerInfo(symbol="AAPL", source="yfinance")
    calls = _serve_analysis(monkeypatch, info, _recent_frame(), THESIS, [_filing()])
    fetched: list[str] = []

    async def recording_fetch(symbol: str) -> list[Filing]:
        fetched.append(symbol)
        return [_filing()]

    monkeypatch.setattr(research, "fetch_latest_filings", recording_fetch)

    response = client.post("/api/v1/stocks/AAPL/analysis", json={"include_filings": False})

    assert response.status_code == 200
    assert fetched == []
    context, _, filings = calls[0]
    assert filings == []
    assert context.notice is None


def _serve_backtest_history(
    monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame | Exception
) -> list[tuple[str, str]]:
    """Fake the backtest route's price fetch; return the (period, interval) requested."""
    calls: list[tuple[str, str]] = []

    async def fake_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        calls.append((period, interval))
        if isinstance(frame, Exception):
            raise frame
        return frame

    monkeypatch.setattr(backtest, "fetch_price_history", fake_history)
    return calls


def _trending_frame(years: int) -> pd.DataFrame:
    """Daily bars over the last ``years`` years: a rise, a fall, then a rise."""
    now = pd.Timestamp.now()
    frame = _daily_frame(now - pd.DateOffset(years=years), now)
    third = len(frame) // 3
    path = [100.0 + i for i in range(third)]
    path += [path[-1] - 0.5 * i for i in range(1, third + 1)]
    path += [path[-1] + i for i in range(1, len(frame) - len(path) + 1)]
    frame["Close"] = path
    return frame


def test_backtest_defaults_to_sma_crossover(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no body, a 50/200 crossover runs on five years of daily bars against buy-and-hold."""
    frame = _trending_frame(5)
    calls = _serve_backtest_history(monkeypatch, frame)

    response = client.post("/api/v1/stocks/spy/backtest")

    assert response.status_code == 200
    body = response.json()
    assert calls == [("5y", "1d")]
    assert body["symbol"] == "SPY"
    assert body["strategy"] == {
        "type": "sma_crossover",
        "fast": 50,
        "slow": 200,
        "allow_short": False,
    }
    assert body["periods_per_year"] == 252
    assert body["cost_bps"] == 5.0
    assert body["coverage"] == "full"
    assert 0 < body["exposure"] < 1
    assert body["trades"] >= 2
    assert len(body["equity_curve"]) == len(frame)
    assert body["equity_curve"][0]["strategy"] == 1.0
    assert body["equity_curve"][-1]["benchmark"] == pytest.approx(
        frame["Close"].iloc[-1] / frame["Close"].iloc[0]
    )
    for metrics in (body["metrics"], body["benchmark"]):
        assert set(metrics) >= {"total_return", "sharpe_ratio", "sortino_ratio", "max_drawdown"}
    assert body["benchmark"]["max_drawdown"] < body["metrics"]["max_drawdown"] <= 0


def test_backtest_buy_and_hold_equals_benchmark(monkeypatch: pytest.MonkeyPatch) -> None:
    """Buy-and-hold without costs scores the same as its benchmark."""
    _serve_backtest_history(monkeypatch, _trending_frame(2))

    response = client.post(
        "/api/v1/stocks/SPY/backtest",
        json={"strategy": {"type": "buy_and_hold"}, "period": "2y", "cost_bps": 0},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["metrics"] == body["benchmark"]
    assert body["trades"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"strategy": {"type": "sma_crossover", "fast": 200, "slow": 50}},
        {"strategy": {"type": "momentum"}},
        {"period": "1mo"},
        {"cost_bps": -1},
    ],
)
def test_backtest_rejects_invalid_requests(payload: dict[str, object]) -> None:
    """Crossed windows, unknown strategies, short periods and negative costs fail validation."""
    assert client.post("/api/v1/stocks/SPY/backtest", json=payload).status_code == 422


def test_backtest_maps_short_history_to_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recent listing too short for the slow average is a 422 that explains itself."""
    now = pd.Timestamp.now()
    _serve_backtest_history(monkeypatch, _daily_frame(now - pd.DateOffset(months=9), now))

    response = client.post("/api/v1/stocks/NEW/backtest", json={"period": "1y"})

    assert response.status_code == 422
    assert "200-bar moving average" in response.json()["detail"]


def test_backtest_maps_unknown_symbol_to_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown symbols are 404s."""
    _serve_backtest_history(monkeypatch, SymbolNotFoundError("Unknown ticker symbol 'ZZZZ'."))

    response = client.post("/api/v1/stocks/ZZZZ/backtest")

    assert response.status_code == 404
