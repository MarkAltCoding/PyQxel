"""Tests for cross-sectional backtests: ranking a universe of stocks against one another.

Price and factor downloads are replaced with fakes, so no test touches the network.
"""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.endpoints import backtest
from app.data.fetcher import SymbolNotFoundError
from app.data.panel import ClosePanel
from app.main import app
from app.models.backtest import (
    CrossSectionalBacktestRequest,
    CrossSectionalMomentum,
    DistanceFromAverage,
    LowVolatility,
    RankingSignal,
    RiskAdjustedMomentum,
    ShortTermReversal,
)
from app.stats.cross_section import (
    rebalance_bars,
    run_cross_sectional_backtest,
    signal_scores,
    universe_prices,
)
from app.stats.volatility import InsufficientDataError

client = TestClient(app)


def _trending(days: int = 400, start: str = "2023-01-02") -> pd.DataFrame:
    """Three stocks compounding steadily: UP gains, FLAT holds, DOWN loses."""
    index = pd.bdate_range(start, periods=days)
    steps = np.arange(days)
    return pd.DataFrame(
        {
            "UP": 100 * 1.002**steps,
            "FLAT": 100 + 0.0 * steps,
            "DOWN": 100 * 0.999**steps,
        },
        index=index,
    )


def _random(symbols: int = 8, days: int = 900, seed: int = 0) -> pd.DataFrame:
    """Noisy closes whose drift rises with the column, so momentum ranks persist."""
    rng = np.random.default_rng(seed)
    drift = np.linspace(-0.0005, 0.001, symbols)
    returns = drift + 0.01 * rng.standard_normal((days, symbols))
    index = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=days)
    return pd.DataFrame(
        100 * np.cumprod(1 + returns, axis=0),
        index=index,
        columns=[f"S{column}" for column in range(symbols)],
    )


MOMENTUM = CrossSectionalMomentum(lookback=60, skip=0)


def test_universe_drops_minority_holidays_and_fills_gaps() -> None:
    """A date only one of four stocks traded is dropped; a lone missing close is carried."""
    closes = _trending(10).assign(MORE=100.0)
    holiday = pd.Timestamp("2023-01-07")  # a Saturday only UP traded
    closes.loc[holiday] = [101.0, np.nan, np.nan, np.nan]
    closes = closes.sort_index()
    closes.loc[pd.Timestamp("2023-01-04"), "FLAT"] = np.nan

    prices = universe_prices(closes)

    assert holiday not in prices.index
    assert prices.loc[pd.Timestamp("2023-01-04"), "FLAT"] == 100.0


def test_a_stock_is_not_priced_before_its_first_close_or_after_its_last() -> None:
    closes = _trending(20)
    closes.iloc[:5, 0] = np.nan
    closes.iloc[15:, 2] = np.nan

    prices = universe_prices(closes)

    assert prices["UP"].iloc[:5].isna().all() and prices["UP"].iloc[5:].notna().all()
    assert prices["DOWN"].iloc[15:].isna().all()


@pytest.mark.parametrize(
    ("signal", "best"),
    [
        (CrossSectionalMomentum(lookback=60, skip=5), "UP"),
        (ShortTermReversal(lookback=21), "DOWN"),
        (DistanceFromAverage(window=50), "UP"),
        (RiskAdjustedMomentum(lookback=60, skip=5), "UP"),
    ],
)
def test_signals_score_the_wanted_stock_highest(signal: RankingSignal, best: str) -> None:
    closes = _trending(200)
    closes["UP"] *= 1 + 0.001 * np.sin(np.arange(200))  # some volatility to divide by

    scores = signal_scores(universe_prices(closes), signal).iloc[-1]

    assert scores.idxmax() == best


def test_low_volatility_prefers_the_calmest_stock() -> None:
    rng = np.random.default_rng(3)
    index = pd.bdate_range("2023-01-02", periods=200)
    closes = pd.DataFrame(
        {
            name: 100 * np.cumprod(1 + scale * rng.standard_normal(200))
            for name, scale in (("CALM", 0.002), ("MID", 0.01), ("WILD", 0.03))
        },
        index=index,
    )

    scores = signal_scores(universe_prices(closes), LowVolatility(window=60)).iloc[-1]

    assert scores.idxmax() == "CALM"


def test_rebalances_fall_on_each_period_end_but_the_last_date() -> None:
    dates = pd.bdate_range("2024-01-01", "2024-04-30")

    monthly = [dates[bar] for bar in rebalance_bars(dates, "monthly")]
    quarterly = [dates[bar] for bar in rebalance_bars(dates, "quarterly")]

    assert [date.month for date in monthly] == [1, 2, 3]
    assert all(date.is_month_end or date.day >= 28 for date in monthly)
    assert quarterly == [pd.Timestamp("2024-03-29")]


def test_top_stock_is_held_from_the_first_rebalance() -> None:
    """Long-only top 1 by momentum holds UP alone, earning exactly its returns."""
    result = run_cross_sectional_backtest(_trending(), MOMENTUM, top_n=1)

    assert result.assets["UP"].long_share == 1.0
    assert result.assets["UP"].weight == 1.0
    assert result.assets["DOWN"].weight == 0.0
    # Bought once at the first rebalance and never traded again.
    assert result.turnover * result.rebalances == pytest.approx(1.0)
    assert result.returns.to_numpy() == pytest.approx(np.full(len(result.returns), 0.002))
    assert result.equity.iloc[0] == 1.0
    assert result.benchmark_equity.index[0] == result.equity.index[0]


def test_long_short_buys_the_winner_and_shorts_the_loser() -> None:
    result = run_cross_sectional_backtest(_trending(), MOMENTUM, top_n=1, long_short=True)

    assert result.assets["UP"].weight == 1.0
    assert result.assets["DOWN"].weight == -1.0
    assert result.assets["DOWN"].short_share == 1.0
    assert result.assets["FLAT"].long_share == result.assets["FLAT"].short_share == 0.0
    # Long 0.2% a day and short a 0.1% daily fall, before the legs drift apart.
    assert result.returns.iloc[0] == pytest.approx(0.003)
    assert result.exposure == pytest.approx(2.0, abs=0.1)


def test_short_proceeds_earn_cash_interest() -> None:
    closes = _trending()
    risk_free = pd.Series(0.0001, index=closes.index)

    plain = run_cross_sectional_backtest(closes, MOMENTUM, top_n=1, long_short=True)
    earning = run_cross_sectional_backtest(
        closes, MOMENTUM, top_n=1, long_short=True, risk_free=risk_free
    )

    first_bar = earning.returns.index[0]
    assert earning.returns[first_bar] - plain.returns[first_bar] == pytest.approx(0.0001)


def test_costs_are_charged_on_capital_traded() -> None:
    closes = _trending()

    free = run_cross_sectional_backtest(closes, MOMENTUM, top_n=1)
    costly = run_cross_sectional_backtest(closes, MOMENTUM, top_n=1, cost_bps=100)

    # The one trade buys a whole unit of capital, paying 1% of it before the next bar.
    assert 1 + costly.returns.iloc[0] == pytest.approx(0.99 * (1 + free.returns.iloc[0]))
    assert free.returns.iloc[1:].to_numpy() == pytest.approx(costly.returns.iloc[1:].to_numpy())


def test_quantile_sets_the_leg_size_and_inverse_volatility_the_weights() -> None:
    closes = _random(symbols=10)

    equal = run_cross_sectional_backtest(closes, MOMENTUM, quantile=0.3)
    inverse = run_cross_sectional_backtest(
        closes, MOMENTUM, quantile=0.3, weighting="inverse_volatility"
    )

    assert equal.holdings_per_leg == 3.0
    held = [part.weight for part in equal.assets.values() if part.weight > 0]
    assert held == pytest.approx([1 / 3] * 3)
    weights = [part.weight for part in inverse.assets.values() if part.weight > 0]
    assert sum(weights) == pytest.approx(1.0)
    assert len(set(np.round(weights, 6))) > 1


def test_holding_the_whole_universe_tracks_the_benchmark() -> None:
    closes = _random(symbols=5)

    result = run_cross_sectional_backtest(closes, MOMENTUM, quantile=1.0, rebalance="weekly")

    # Weekly rather than daily rebalancing leaves only a small tracking difference.
    assert result.metrics.total_return == pytest.approx(result.benchmark.total_return, abs=0.03)
    assert result.exposure == pytest.approx(1.0)


def test_persistent_ranks_give_a_positive_information_coefficient() -> None:
    result = run_cross_sectional_backtest(_random(symbols=10, days=1500), MOMENTUM)

    assert result.information_coefficient is not None
    assert result.information_coefficient.mean > 0
    assert result.information_coefficient.observations == result.rebalances


def test_late_listings_join_the_ranking_once_they_have_history() -> None:
    closes = _random(symbols=6)
    closes.iloc[:400, 0] = np.nan

    result = run_cross_sectional_backtest(closes, MOMENTUM)

    listed = pd.Timestamp(closes.index[400])
    joined = result.assets["S0"].first_ranked
    assert joined is not None and joined >= listed + pd.Timedelta(days=60)
    assert result.equity.index[0] < listed


def test_too_short_a_window_names_the_warmup_needed() -> None:
    with pytest.raises(InsufficientDataError, match="61 daily closes"):
        run_cross_sectional_backtest(_trending(50), MOMENTUM)


def test_request_rejects_legs_that_do_not_fit() -> None:
    symbols = ["AAA", "BBB", "CCC", "DDD"]
    with pytest.raises(ValidationError, match="quantile"):
        CrossSectionalBacktestRequest(symbols=symbols, long_short=True, quantile=0.6)
    with pytest.raises(ValidationError, match="top_n"):
        CrossSectionalBacktestRequest(symbols=symbols, long_short=True, top_n=3)
    with pytest.raises(ValidationError, match="unique"):
        CrossSectionalBacktestRequest(symbols=["AAA", "aaa", "BBB"])
    assert CrossSectionalBacktestRequest(symbols=symbols, top_n=4).top_n == 4


def _serve(monkeypatch: pytest.MonkeyPatch, closes: pd.DataFrame | Exception) -> list[str]:
    """Fake the panel and factor downloads; return the symbols requested."""
    requested: list[str] = []

    async def fake_panel(
        symbols: list[str], period: str, interval: str, tolerate_failures: bool = False
    ) -> ClosePanel:
        requested.extend(symbols)
        if isinstance(closes, Exception):
            raise closes
        return ClosePanel(
            closes=closes, timezones={symbol: "America/New_York" for symbol in symbols}
        )

    async def fake_factors(model: str) -> pd.DataFrame:
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=2000)
        rng = np.random.default_rng(1)
        table = pd.DataFrame(
            rng.normal(0, 0.01, (len(dates), 4)),
            index=dates,
            columns=["Mkt-RF", "SMB", "HML", "Mom"],
        )
        table["RF"] = 0.0001
        return table

    monkeypatch.setattr(backtest, "fetch_close_panel", fake_panel)
    monkeypatch.setattr(backtest, "fetch_factors", fake_factors)
    return requested


def test_cross_sectional_route_ranks_the_universe(monkeypatch: pytest.MonkeyPatch) -> None:
    closes = _random(symbols=8)
    closes.iloc[:500, 7] = np.nan
    requested = _serve(monkeypatch, closes)

    response = client.post(
        "/api/v1/backtests/cross-sectional",
        json={
            "symbols": [str(column).lower() for column in closes.columns],
            "signal": {"type": "momentum", "lookback": 126, "skip": 21},
            "quantile": 0.25,
            "long_short": True,
            "cash_interest": True,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert requested == list(closes.columns)
    # Seven stocks are ranked at first, so one per leg; two once S7 joins.
    assert 1.0 < body["holdings_per_leg"] < 2.0
    assert sum(asset["weight"] > 0 for asset in body["assets"]) == 2
    assert [asset["symbol"] for asset in body["assets"]] == list(closes.columns)
    assert sum(asset["weight"] for asset in body["assets"]) == pytest.approx(0.0)
    assert body["attribution"]["model"] == "carhart4"
    assert body["information_coefficient"]["observations"] == body["rebalances"]
    assert body["equity_curve"][0]["strategy"] == 1.0
    assert len(body["equity_curve"]) == body["observations"] + 1
    assert "S7" in body["notice"]
    assert "delisted" in body["disclaimer"]


def test_cross_sectional_route_reports_unknown_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, SymbolNotFoundError("Unknown ticker symbols: NOPE."))

    response = client.post(
        "/api/v1/backtests/cross-sectional", json={"symbols": ["AAA", "BBB", "NOPE"]}
    )

    assert response.status_code == 404
    assert "NOPE" in response.json()["detail"]


def test_cross_sectional_route_needs_the_signal_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, _random(symbols=4, days=200))

    response = client.post(
        "/api/v1/backtests/cross-sectional",
        json={"symbols": ["S0", "S1", "S2", "S3"], "attribution": None},
    )

    assert response.status_code == 422
    assert "253 daily closes" in response.json()["detail"]
