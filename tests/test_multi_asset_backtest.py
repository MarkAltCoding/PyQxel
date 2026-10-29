"""Tests for custom strategies that allocate between several symbols.

The weights are computed in the browser; here they are posted as the page would post
them. Price and factor downloads are replaced with fakes, so no test touches the network.
"""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import backtest
from app.data.fetcher import SymbolNotFoundError
from app.data.panel import ClosePanel
from app.main import app
from app.stats.backtest import MisalignedPositionsError, replay_weights

client = TestClient(app)

ROUTE = "/api/v1/backtests/custom"


def _closes(days: int = 300) -> pd.DataFrame:
    """GOOG rising 0.2% a day and UNH falling 0.1% a day, ending today."""
    index = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=days)
    steps = np.arange(days)
    return pd.DataFrame({"GOOG": 100 * 1.002**steps, "UNH": 100 * 0.999**steps}, index=index)


def _serve(monkeypatch: pytest.MonkeyPatch, closes: pd.DataFrame | Exception) -> None:
    async def fake_panel(symbols: list[str], period: str, interval: str) -> ClosePanel:
        if isinstance(closes, Exception):
            raise closes
        return ClosePanel(
            closes=closes[symbols], timezones={symbol: "America/New_York" for symbol in symbols}
        )

    async def fake_factors(model: str) -> pd.DataFrame:
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=2000)
        table = pd.DataFrame(
            np.random.default_rng(1).normal(0, 0.01, (len(dates), 4)),
            index=dates,
            columns=["Mkt-RF", "SMB", "HML", "Mom"],
        )
        table["RF"] = 0.0001
        return table

    monkeypatch.setattr(backtest, "fetch_close_panel", fake_panel)
    monkeypatch.setattr(backtest, "fetch_factors", fake_factors)


def _body(index: pd.Index, weights: list[list[float]], **options: object) -> dict[str, object]:
    return {
        "symbols": ["GOOG", "UNH"],
        "code": "def strategy(bars):\n    ...\n",
        "dates": [day.date().isoformat() for day in index],
        "weights": weights,
        "cost_bps": 0,
        "attribution": None,
    } | options


def test_replay_keeps_the_common_dates_in_the_weights_range() -> None:
    closes = _closes(10)
    closes.iloc[3, 1] = np.nan  # UNH did not trade that day
    dates = [day.date() for day in closes.index[:8]]

    prices, held = replay_weights(closes, dates, [[0.5, 0.5]] * 8)

    assert list(prices.index) == list(closes.index[:8].delete(3))
    assert held.shape == (7, 2) and (held == 0.5).all().all()


def test_replay_needs_weights_for_every_common_date() -> None:
    closes = _closes(10)
    dates = [day.date() for day in closes.index.delete(4)]

    with pytest.raises(MisalignedPositionsError, match="1 bars every symbol traded"):
        replay_weights(closes, dates, [[1.0, 0.0]] * len(dates))


def test_all_in_one_symbol_earns_its_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    closes = _closes()
    _serve(monkeypatch, closes)
    weights = [[0.0, 0.0]] + [[1.0, 0.0]] * (len(closes) - 1)

    response = client.post(ROUTE, json=_body(closes.index, weights))

    assert response.status_code == 200, response.text
    body = response.json()
    # Bought at the first close, so every later return is GOOG's.
    assert body["metrics"]["total_return"] == pytest.approx(1.002 ** (len(closes) - 2) - 1)
    assert body["symbol"] == "GOOG" and body["symbols"] == ["GOOG", "UNH"]
    assert [asset["symbol"] for asset in body["assets"]] == ["GOOG", "UNH"]
    assert body["assets"][1]["exposure"] == 0.0
    assert body["strategy"]["type"] == "custom"


def test_reallocating_between_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    """Long the winner and short the loser earns both, against holding both equally."""
    closes = _closes()
    _serve(monkeypatch, closes)
    weights = [[1.0, -1.0]] * len(closes)

    response = client.post(ROUTE, json=_body(closes.index, weights, cash_interest=True))

    assert response.status_code == 200, response.text
    body = response.json()
    days = len(closes) - 1
    # 0.2% on GOOG, 0.1% from UNH's fall and 0.01% interest on the cash left over.
    assert body["metrics"]["total_return"] == pytest.approx(1.0031**days - 1, rel=1e-3)
    equal = (1.0005**days) - 1
    assert body["benchmark"]["total_return"] == pytest.approx(equal, rel=1e-2)
    assert body["exposure"] == pytest.approx(2.0, abs=0.01)


def test_multi_asset_results_are_stored_and_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    closes = _closes()
    _serve(monkeypatch, closes)

    created = client.post(
        ROUTE, json=_body(closes.index, [[0.5, 0.5]] * len(closes), language="cpp")
    ).json()

    assert created["id"] is not None
    stored = client.get(f"/api/v1/backtests/{created['id']}").json()
    assert stored["symbols"] == ["GOOG", "UNH"]
    assert stored["strategy"] == {
        "type": "custom",
        "language": "cpp",
        "code": created["strategy"]["code"],
    }
    assert stored["assets"] == created["assets"]
    listed = client.get("/api/v1/backtests").json()["items"]
    assert listed[0]["symbols"] == ["GOOG", "UNH"]


def test_late_starting_symbol_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    closes = _closes(1300)
    closes.iloc[:600, 1] = np.nan
    _serve(monkeypatch, closes)
    common = closes.dropna().index

    response = client.post(ROUTE, json=_body(common, [[0.5, 0.5]] * len(common), period="5y"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["coverage"] == "partial"
    assert "UNH" in body["notice"]


def test_weights_missing_on_a_common_bar_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    closes = _closes()
    _serve(monkeypatch, closes)
    gap = closes.index.delete(100)

    response = client.post(ROUTE, json=_body(gap, [[1.0, 0.0]] * len(gap)))

    assert response.status_code == 422
    assert "have no weights" in response.json()["detail"]


def test_unknown_symbols_are_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, SymbolNotFoundError("Unknown ticker symbols: UNH."))
    closes = _closes(5)

    response = client.post(ROUTE, json=_body(closes.index, [[1.0, 0.0]] * 5))

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"weights": [[1.5, 1.0]] * 5}, "sum to more than 2"),
        ({"weights": [[1.0]] * 5}, "one per symbol"),
        ({"weights": [[1.0, 0.0]] * 4}, "same length"),
        ({"symbols": ["GOOG"]}, "at least 2"),
        ({"symbols": ["GOOG", "goog"]}, "unique"),
    ],
)
def test_invalid_requests_are_rejected(change: dict[str, object], message: str) -> None:
    closes = _closes(5)

    response = client.post(ROUTE, json=_body(closes.index, [[1.0, 0.0]] * 5) | change)

    assert response.status_code == 422
    assert message in response.text
