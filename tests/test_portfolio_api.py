"""Tests for the multi-asset routes.

Price downloads are replaced with fakes, so no test touches the network.
"""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import portfolio
from app.data.fetcher import DataFetchError, SymbolNotFoundError
from app.data.panel import ClosePanel
from app.main import app

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

    async def fake_panel(symbols: list[str], period: str, interval: str) -> ClosePanel:
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


@pytest.mark.parametrize(
    "payload",
    [
        {"symbols": ["SPY"]},
        {"symbols": ["SPY", "spy"]},
        {"symbols": [f"S{i}" for i in range(21)]},
        {"symbols": ["SPY", "QQQ"], "period": "6mo"},
        {},
    ],
)
def test_invalid_requests_are_rejected(payload: dict[str, object]) -> None:
    """One symbol, repeats, too many symbols, short periods and missing symbols fail."""
    assert client.post("/api/v1/portfolio/copula", json=payload).status_code == 422
