"""Tests for the multi-asset routes.

Price downloads are replaced with fakes, so no test touches the network.
"""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import backtest, portfolio
from app.data.fetcher import DataFetchError, SymbolNotFoundError
from app.data.panel import ClosePanel
from app.main import app
from app.models.portfolio import MAX_SYMBOLS
from app.models.stock import TickerInfo

client = TestClient(app)


def _closes(
    symbols: list[str], days: int = 600, seed: int = 0, df: float | None = 3.0
) -> pd.DataFrame:
    """Correlated closes ending today, with t-copula (``df``) or Gaussian (``None``) returns."""
    rng = np.random.default_rng(seed)
    dimension = len(symbols)
    correlation = np.full((dimension, dimension), 0.5) + 0.5 * np.eye(dimension)
    shocks = rng.multivariate_normal(np.zeros(dimension), correlation, size=days)
    if df is not None:
        shocks /= np.sqrt(rng.chisquare(df, size=days) / df)[:, None]
    index = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=days)
    return pd.DataFrame(100 * np.cumprod(1 + 0.01 * shocks, axis=0), index=index, columns=symbols)


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    result: pd.DataFrame | Exception,
    timezones: dict[str, str | None] | None = None,
) -> list[tuple[list[str], str]]:
    """Fake the panel download; return the (symbols, period) requested."""
    calls: list[tuple[list[str], str]] = []

    async def fake_panel(
        symbols: list[str], period: str, interval: str, tolerate_failures: bool = False
    ) -> ClosePanel:
        calls.append((symbols, period))
        if isinstance(result, Exception):
            raise result
        zones = timezones or {symbol: "America/New_York" for symbol in symbols}
        return ClosePanel(closes=result, timezones=zones)

    monkeypatch.setattr(portfolio, "fetch_close_panel", fake_panel)
    return calls


def test_copula_fit_returns_both_copulas_and_every_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fat-tailed joint returns favor the t copula; matrices follow the symbol order."""
    calls = _serve(monkeypatch, _closes(["SPY", "QQQ", "IWM"], days=1_310))

    response = client.post("/api/v1/portfolio/copula", json={"symbols": ["spy", "qqq", "iwm"]})

    assert response.status_code == 200
    body = response.json()
    assert calls == [(["SPY", "QQQ", "IWM"], "5y")]
    assert body["symbols"] == ["SPY", "QQQ", "IWM"]
    assert body["observations"] == 1_309
    for matrix in (
        body["kendall_tau"],
        body["gaussian"]["correlation"],
        body["student_t"]["correlation"],
    ):
        assert len(matrix) == 3 and all(len(row) == 3 for row in matrix)
    assert body["preferred"] == "student_t"
    assert body["aic_difference"] == pytest.approx(
        body["gaussian"]["aic"] - body["student_t"]["aic"]
    )
    assert 2 < body["student_t"]["degrees_of_freedom"] < 6
    assert [pair["symbols"] for pair in body["pairs"]] == [
        ["SPY", "QQQ"],
        ["SPY", "IWM"],
        ["QQQ", "IWM"],
    ]
    assert body["pairs"][0]["tail_dependence"] > 0.1
    assert body["warnings"] == []
    assert body["notice"] is None


def test_gaussian_returns_are_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without fat joint tails, the warnings say the t copula adds nothing.

    Near the bound the fitted degrees of freedom are noisy, so the seed is fixed to a
    sample that reaches it.
    """
    _serve(monkeypatch, _closes(["A", "B"], days=400, df=None, seed=1))

    body = client.post("/api/v1/portfolio/copula", json={"symbols": ["A", "B"]}).json()

    assert body["student_t"]["degrees_of_freedom_at_bound"] is True
    assert body["preferred"] == "gaussian"
    assert any("upper bound" in warning for warning in body["warnings"])
    assert any("do not clearly favor" in warning for warning in body["warnings"])


def test_markets_hours_apart_are_warned_about(monkeypatch: pytest.MonkeyPatch) -> None:
    """New York and Tokyo listings carry the asynchronous trading warning."""
    _serve(
        monkeypatch,
        _closes(["SPY", "7203.T"]),
        {"SPY": "America/New_York", "7203.T": "Asia/Tokyo"},
    )

    body = client.post("/api/v1/portfolio/copula", json={"symbols": ["SPY", "7203.T"]}).json()

    assert any("time zones hours apart" in warning for warning in body["warnings"])


def test_late_listing_shortens_the_window_with_a_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    """When one asset starts late, the fit starts with it and the notice names it."""
    closes = _closes(["OLD", "NEW"], days=1_250)
    closes.loc[closes.index[:900], "NEW"] = np.nan
    _serve(monkeypatch, closes)

    body = client.post(
        "/api/v1/portfolio/copula", json={"symbols": ["OLD", "NEW"], "period": "5y"}
    ).json()

    assert body["observations"] == 349
    assert body["excluded_dates"] == 900
    assert "because NEW has no earlier data" in body["notice"]


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (SymbolNotFoundError("Unknown ticker symbols: ZZZZ."), 404),
        (DataFetchError("Could not fetch price history for MSFT."), 502),
    ],
)
def test_download_failures_map_to_statuses(
    monkeypatch: pytest.MonkeyPatch, error: Exception, status: int
) -> None:
    """Unknown symbols are 404s and provider failures 502s, with the message passed on."""
    _serve(monkeypatch, error)

    response = client.post("/api/v1/portfolio/copula", json={"symbols": ["AAPL", "MSFT"]})

    assert response.status_code == status
    assert response.json() == {"detail": str(error)}


def test_too_little_shared_history_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assets sharing fewer than the minimum days are rejected with the reason."""
    _serve(monkeypatch, _closes(["A", "B"], days=150))

    response = client.post("/api/v1/portfolio/copula", json={"symbols": ["A", "B"]})

    assert response.status_code == 422
    assert "daily closes" in response.json()["detail"]


def _equities(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake ticker info classing every symbol as a stock."""

    async def info(symbol: str) -> TickerInfo:
        return TickerInfo(symbol=symbol, quote_type="EQUITY", source="test")

    monkeypatch.setattr(portfolio, "fetch_ticker_info", info)


def test_holdings_the_fit_cannot_use_are_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cash, recent listings, and failed downloads are listed; the rest are fitted."""
    closes = _closes(["SPY", "QQQ", "IWM", "NEW"], days=1_310)
    closes.loc[closes.index[:-170], "NEW"] = np.nan
    calls: list[list[str]] = []

    async def fake_panel(
        symbols: list[str], period: str, interval: str, tolerate_failures: bool = False
    ) -> ClosePanel:
        calls.append(symbols)
        assert tolerate_failures
        kept = [symbol for symbol in symbols if symbol != "GONE"]
        return ClosePanel(
            closes=closes[kept],
            timezones={symbol: "America/New_York" for symbol in kept},
            failed={"GONE": "unknown symbol"},
        )

    monkeypatch.setattr(portfolio, "fetch_close_panel", fake_panel)
    _equities(monkeypatch)

    body = client.post(
        "/api/v1/portfolio/copula",
        json={"symbols": ["SPY", "SPAXX", "QQQ", "NEW", "GONE", "IWM"]},
    ).json()

    assert calls == [["SPY", "QQQ", "NEW", "GONE", "IWM"]]
    assert body["symbols"] == ["SPY", "QQQ", "IWM"]
    assert [(h["symbol"], h["reason"]) for h in body["left_out"]] == [
        ("SPAXX", "cash"),
        ("NEW", "short_history"),
        ("GONE", "no_data"),
    ]
    assert len(body["pairs"]) == 3


def test_a_late_start_beyond_the_cutoff_is_left_out_and_pairs_keep_their_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a cutoff, a listing that would halve the window is left out; without one it
    is kept, and the pairs that predate it are also measured over their own history."""
    closes = _closes(["SPY", "QQQ", "NEW"], days=1_250)
    closes.loc[closes.index[:900], "NEW"] = np.nan
    _serve(monkeypatch, closes)
    _equities(monkeypatch)

    cut = client.post(
        "/api/v1/portfolio/copula",
        json={"symbols": ["SPY", "QQQ", "NEW"], "max_history_cut": 0.5},
    ).json()
    kept = client.post("/api/v1/portfolio/copula", json={"symbols": ["SPY", "QQQ", "NEW"]}).json()

    assert cut["symbols"] == ["SPY", "QQQ"]
    assert [(h["symbol"], h["reason"]) for h in cut["left_out"]] == [("NEW", "late_start")]
    assert cut["observations"] == 1_249
    assert kept["symbols"] == ["SPY", "QQQ", "NEW"] and kept["observations"] == 349
    own = {tuple(pair["symbols"]): pair["own_observations"] for pair in kept["pairs"]}
    assert own == {("SPY", "QQQ"): 1_249, ("SPY", "NEW"): 349, ("QQQ", "NEW"): 349}


@pytest.mark.parametrize("cut", [0, 1, 1.5])
def test_the_cutoff_must_be_a_fraction(cut: float) -> None:
    """A cutoff outside (0, 1) is rejected."""
    response = client.post(
        "/api/v1/portfolio/copula", json={"symbols": ["SPY", "QQQ"], "max_history_cut": cut}
    )

    assert response.status_code == 422


def test_fewer_than_two_usable_holdings_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """With one holding left to compare, the reason names those left out."""
    closes = _closes(["SPY", "NEW"], days=1_310)
    closes.loc[closes.index[:-50], "NEW"] = np.nan
    _serve(monkeypatch, closes)
    _equities(monkeypatch)

    response = client.post("/api/v1/portfolio/copula", json={"symbols": ["SPY", "NEW", "SPAXX"]})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "NEW: 50 daily closes" in detail and "SPAXX: a money-market fund" in detail


@pytest.mark.parametrize(
    "payload",
    [
        {"symbols": ["SPY"]},
        {"symbols": ["SPY", "spy"]},
        {"symbols": [f"S{i}" for i in range(MAX_SYMBOLS + 1)]},
        {"symbols": ["SPY", "QQQ"], "period": "6mo"},
        {},
    ],
)
def test_invalid_requests_are_rejected(payload: dict[str, object]) -> None:
    """One symbol, repeats, too many symbols, short periods, and missing symbols fail."""
    assert client.post("/api/v1/portfolio/copula", json=payload).status_code == 422


def _factors(dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Carhart factors and a 0.01% daily risk-free rate on ``dates``."""
    rng = np.random.default_rng(1)
    table = pd.DataFrame(
        rng.normal(0, 0.01, (len(dates), 4)), index=dates, columns=["Mkt-RF", "SMB", "HML", "Mom"]
    )
    table["RF"] = 0.0001
    return table


def _serve_factors(monkeypatch: pytest.MonkeyPatch, closes: pd.DataFrame) -> None:
    async def fake_factors(model: str) -> pd.DataFrame:
        return _factors(pd.DatetimeIndex(closes.index))

    monkeypatch.setattr(backtest, "fetch_factors", fake_factors)


def test_portfolio_backtest_runs_the_strategy_on_each_holding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each holding is traded on its own prices; contributions and attribution are given."""
    closes = _closes(["SPY", "TLT"], days=700)
    _serve(monkeypatch, closes)
    _serve_factors(monkeypatch, closes)

    response = client.post(
        "/api/v1/portfolio/backtest",
        json={
            "holdings": [{"symbol": "SPY", "weight": 0.6}, {"symbol": "TLT", "weight": 0.4}],
            "strategy": {"type": "time_series_momentum", "lookback": 126},
            "period": "5y",
            "cash_interest": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [asset["symbol"] for asset in body["assets"]] == ["SPY", "TLT"]
    assert [asset["weight"] for asset in body["assets"]] == [0.6, 0.4]
    assert all(asset["exposure"] < 1 for asset in body["assets"])
    assert body["observations"] == 699
    assert body["attribution"]["model"] == "carhart4"
    assert len(body["equity_curve"]) == 700
    assert body["equity_curve"][0]["strategy"] == 1.0


def test_portfolio_buy_and_hold_matches_its_benchmark(monkeypatch: pytest.MonkeyPatch) -> None:
    closes = _closes(["SPY", "TLT", "GLD"], days=400)
    _serve(monkeypatch, closes)
    _serve_factors(monkeypatch, closes)

    response = client.post(
        "/api/v1/portfolio/backtest",
        json={
            "holdings": [
                {"symbol": "SPY", "weight": 0.5},
                {"symbol": "TLT", "weight": 0.3},
                {"symbol": "GLD", "weight": 0.2},
            ],
            "strategy": {"type": "buy_and_hold"},
            "period": "2y",
            "cost_bps": 0,
            "attribution": None,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["metrics"]["total_return"] == pytest.approx(body["benchmark"]["total_return"])
    assert body["exposure"] == 1.0
    assert body["attribution"] is None


def test_portfolio_backtest_needs_enough_history_for_the_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closes = _closes(["SPY", "TLT"], days=260)
    _serve(monkeypatch, closes)
    _serve_factors(monkeypatch, closes)

    response = client.post(
        "/api/v1/portfolio/backtest",
        json={
            "holdings": [{"symbol": "SPY", "weight": 0.5}, {"symbol": "TLT", "weight": 0.5}],
            "strategy": {"type": "time_series_momentum", "lookback": 252},
            "period": "1y",
        },
    )

    assert response.status_code == 422
    assert "252-bar momentum lookback" in response.json()["detail"]


def test_portfolio_dual_momentum_compares_each_holding_with_one_benchmark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The benchmark's daily closes are fetched once for every holding."""
    closes = _closes(["SPY", "TLT"], days=700)
    _serve(monkeypatch, closes)
    _serve_factors(monkeypatch, closes)
    fetched: list[tuple[str, str, str]] = []

    async def fake_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        fetched.append((symbol, period, interval))
        return closes[["SPY"]].rename(columns={"SPY": "Close"})

    monkeypatch.setattr(backtest, "fetch_price_history", fake_history)

    response = client.post(
        "/api/v1/portfolio/backtest",
        json={
            "holdings": [{"symbol": "SPY", "weight": 0.6}, {"symbol": "TLT", "weight": 0.4}],
            "strategy": {"type": "dual_momentum", "lookback": 126},
            "period": "5y",
        },
    )

    assert response.status_code == 200
    assert fetched == [("SPY", "5y", "1d")]
    assert response.json()["strategy"]["benchmark"] == "SPY"


# Each holding's gain or loss over a window, and its 52-week range.


def _bars(days: int, jump_days_ago: int | None = None) -> pd.DataFrame:
    """Daily bars ending on the last business day: 100 until ``jump_days_ago`` calendar
    days before the end, then 125; highs and lows 10% either side."""
    index = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=days, tz="America/New_York")
    closes = pd.Series(100.0, index=index)
    if jump_days_ago is not None:
        closes[index > index[-1] - pd.Timedelta(days=jump_days_ago)] = 125.0
    return pd.DataFrame({"Close": closes, "High": closes * 1.1, "Low": closes * 0.9})


def _serve_histories(
    monkeypatch: pytest.MonkeyPatch, histories: dict[str, pd.DataFrame | Exception]
) -> list[tuple[str, str]]:
    """Fake each symbol's history download; return the (symbol, period) requested."""
    calls: list[tuple[str, str]] = []

    async def fake(symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        calls.append((symbol, period))
        result = histories[symbol]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(portfolio, "fetch_price_history", fake)
    return calls


def test_changes_cover_each_window_and_say_why_one_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _serve_histories(
        monkeypatch,
        {
            "AAPL": _bars(600, jump_days_ago=200),
            "NEWCO": _bars(40),
            "GONE": DataFetchError("Provider timed out."),
        },
    )

    year = client.get(
        "/api/v1/portfolio/changes", params={"symbols": "aapl,NEWCO,SPAXX,GONE", "window": "1y"}
    )
    month = client.get("/api/v1/portfolio/changes", params={"symbols": "AAPL", "window": "1mo"})

    assert year.status_code == 200
    body = year.json()
    assert body["window"] == "1y"
    changes = {c["symbol"]: c for c in body["changes"]}
    assert list(changes) == ["AAPL", "NEWCO", "SPAXX", "GONE"]
    assert changes["AAPL"]["change"] == pytest.approx(0.25)
    assert changes["NEWCO"]["unavailable"] == "too_new"
    assert "after the window starts" in changes["NEWCO"]["detail"]
    assert changes["SPAXX"]["unavailable"] == "cash"
    assert changes["GONE"] == {**changes["GONE"], "unavailable": "no_data", "change": None}
    assert month.json()["changes"][0]["change"] == pytest.approx(0.0)
    # Cash is never downloaded; a year shares the 2-year download, a month the 3-month one.
    assert ("SPAXX", "2y") not in calls
    assert ("AAPL", "2y") in calls and ("AAPL", "3mo") in calls


def test_changes_since_a_chosen_day(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_histories(monkeypatch, {"AAPL": _bars(900, jump_days_ago=200)})
    since = (pd.Timestamp.now() - pd.Timedelta(days=500)).date().isoformat()

    body = client.get("/api/v1/portfolio/changes", params={"symbols": "AAPL", "since": since})

    assert body.status_code == 200
    assert body.json()["window"] is None and body.json()["start"] == since
    assert body.json()["changes"][0]["change"] == pytest.approx(0.25)


@pytest.mark.parametrize(
    "params",
    [
        {"symbols": "AAPL", "since": "2999-01-01"},
        {"symbols": "AAPL", "since": "1960-01-01"},
        {"symbols": "AAPL;DROP"},
        {"symbols": ",".join(f"S{i}" for i in range(MAX_SYMBOLS + 1))},
        {"symbols": "AAPL", "window": "7y"},
    ],
)
def test_changes_reject_bad_requests(params: dict[str, str]) -> None:
    assert client.get("/api/v1/portfolio/changes", params=params).status_code == 422


def test_changes_fail_with_the_provider_status_when_nothing_can_be_priced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve_histories(monkeypatch, {"AAPL": SymbolNotFoundError("Unknown symbol AAPL.")})
    response = client.get("/api/v1/portfolio/changes", params={"symbols": "AAPL"})
    assert response.status_code == 404


def test_ranges_span_52_weeks_with_the_latest_close(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _serve_histories(
        monkeypatch, {"AAPL": _bars(600, jump_days_ago=200), "GONE": DataFetchError("Timed out.")}
    )

    body = client.get("/api/v1/portfolio/ranges", params={"symbols": "AAPL,SPAXX,GONE"}).json()

    ranges = {r["symbol"]: r for r in body["ranges"]}
    assert ranges["AAPL"]["low"] == pytest.approx(90.0)
    assert ranges["AAPL"]["high"] == pytest.approx(137.5)
    assert ranges["AAPL"]["latest"] == pytest.approx(125.0)
    assert ranges["SPAXX"]["unavailable"] == "cash"
    assert ranges["GONE"]["unavailable"] == "no_data"
    assert calls == [("AAPL", "2y"), ("GONE", "2y")]
