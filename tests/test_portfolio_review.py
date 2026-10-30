"""Tests for Claude's review of the saved portfolio: its statistics, routes and storage.

Claude and the price providers are faked.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.ai.agent import AIRefusalError, Written
from app.api.v1.endpoints import portfolio
from app.data.panel import ClosePanel
from app.db.session import get_sessionmaker
from app.db.users import User, ai_usage, create_api_key
from app.main import app
from app.models.portfolio_analysis import PortfolioContext, PortfolioReview, TargetPosition
from app.models.research import AnalysisDelta, RiskFactor
from app.models.stock import TickerInfo
from app.stats.portfolio_risk import portfolio_stats, ranked_pairs
from app.stats.rebalance import Target, plan_trades
from tests.test_api import _sse_events

client = TestClient(app)


def _signed_in(user: User) -> dict[str, str]:
    """Headers of a real API key for ``user``, with an Anthropic key, as production sends."""

    async def issue() -> str:
        async with get_sessionmaker()() as session:
            return (await create_api_key(session, user.id, "test")).key

    return {"Authorization": f"Bearer {asyncio.run(issue())}", "X-Anthropic-Key": "sk-ant-test-key"}


REVIEW = PortfolioReview(
    headline="Concentrated in technology.",
    risk_level="elevated",
    diversification="fair",
    summary="Two of three holdings are technology stocks.",
    strengths=["Low correlation of XOM with the rest."],
    key_risks=[RiskFactor(name="Sector concentration", severity="high", evidence="70% tech.")],
    plan_summary="Trim technology into an international fund.",
    target_allocation=[
        TargetPosition(symbol="AAPL", target_weight=0.25, order_type="market", rationale="Trim."),
        TargetPosition(symbol="MSFT", target_weight=0.25, order_type="market", rationale="Trim."),
        TargetPosition(symbol="XOM", target_weight=0.3, order_type="market", rationale="Trim."),
        TargetPosition(
            symbol="vxus",
            target_weight=0.2,
            order_type="limit",
            limit_offset_percent=-0.5,
            rationale="Adds international stocks.",
        ),
    ],
    considerations=["Selling in a taxable account realizes gains."],
    data_limitations=["No valuations."],
)

PRICES = {"AAPL": 200.0, "MSFT": 400.0, "XOM": 100.0, "VXUS": 50.0}
SECTORS = {"AAPL": "Technology", "MSFT": "Technology", "XOM": "Energy", "VXUS": None}


def _returns(days: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    market = rng.normal(0.0004, 0.01, days)
    index = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=days)
    return pd.DataFrame(
        {
            "AAPL": market + rng.normal(0, 0.008, days),
            "MSFT": market + rng.normal(0, 0.008, days),
            "XOM": rng.normal(0.0002, 0.012, days),
        },
        index=index,
    )


# Statistics.


def test_risk_contributions_sum_to_one_and_concentration_is_measured() -> None:
    returns = _returns()
    weights = pd.Series({"AAPL": 0.5, "MSFT": 0.3, "XOM": 0.2})

    stats = portfolio_stats(returns, weights)

    assert sum(a.risk_contribution for a in stats.assets.values()) == pytest.approx(1.0)
    assert stats.effective_holdings == pytest.approx(1 / (0.25 + 0.09 + 0.04))
    assert stats.diversification_ratio > 1.0
    assert stats.max_drawdown <= stats.current_drawdown <= 0.0
    tech, energy = stats.assets["AAPL"].correlation_to_rest, stats.assets["XOM"].correlation_to_rest
    assert tech is not None and energy is not None
    assert tech > energy


def test_identical_holdings_do_not_diversify() -> None:
    returns = _returns()
    returns["MSFT"] = returns["AAPL"]

    stats = portfolio_stats(returns[["AAPL", "MSFT"]], pd.Series({"AAPL": 0.5, "MSFT": 0.5}))

    assert stats.diversification_ratio == pytest.approx(1.0)
    assert stats.average_correlation == pytest.approx(1.0)


def test_a_single_holding_has_no_correlations() -> None:
    stats = portfolio_stats(_returns()[["XOM"]], pd.Series({"XOM": 1.0}))

    assert stats.average_correlation is None
    assert stats.assets["XOM"].correlation_to_rest is None
    assert stats.assets["XOM"].risk_contribution == pytest.approx(1.0)


def test_cash_is_held_without_correlations() -> None:
    returns = _returns()[["AAPL", "XOM"]].assign(SPAXX=0.0)
    weights = pd.Series({"AAPL": 0.4, "XOM": 0.4, "SPAXX": 0.2})

    stats = portfolio_stats(returns, weights)

    assert list(stats.correlations.columns) == ["AAPL", "XOM"]
    assert stats.average_correlation is not None
    cash = stats.assets["SPAXX"]
    assert cash.correlation_to_rest is None
    assert (cash.volatility, cash.risk_contribution, cash.max_drawdown) == (0.0, 0.0, 0.0)
    assert (
        stats.volatility
        < portfolio_stats(returns[["AAPL", "XOM"]], pd.Series({"AAPL": 0.5, "XOM": 0.5})).volatility
    )


def test_pairs_are_ranked_without_repeating() -> None:
    most, least = ranked_pairs(_returns().corr(), 1)

    assert most[0][:2] == ("AAPL", "MSFT")
    assert least[0] != most[0]


# Trade sizing.


def test_trades_reach_the_targets_in_whole_shares() -> None:
    holdings = {"AAPL": (10.0, 200.0), "XOM": (20.0, 100.0)}  # 2,000 and 2,000
    targets = {
        "AAPL": Target(0.25, "market", None, "Trim."),
        "XOM": Target(0.5, "market", None, "Keep."),
        "VXUS": Target(0.25, "limit", -0.5, "Add."),
    }

    plan = plan_trades(holdings, targets, {"VXUS": 50.0}, cash=1_000.0)

    trades = {t.symbol: t for t in plan.trades}
    assert plan.total_value == 5_000.0
    assert (trades["AAPL"].action, trades["AAPL"].shares) == ("sell", 4)  # 2,000 to 1,250
    assert (trades["XOM"].action, trades["XOM"].shares) == ("buy", 5)  # 2,000 to 2,500
    assert (trades["VXUS"].action, trades["VXUS"].shares) == ("buy", 25)
    assert trades["VXUS"].limit_price == 49.75
    assert [t.action for t in plan.trades] == ["sell", "buy", "buy"]
    assert plan.cash_after == pytest.approx(1_000 + 800 - 500 - 1_250)


def test_a_zero_target_sells_everything_and_omitted_holdings_stay() -> None:
    holdings = {"AAPL": (10.0, 200.0), "XOM": (20.0, 100.0)}

    plan = plan_trades(holdings, {"AAPL": Target(0.0, "market", None, "Exit.")}, {})

    assert [(t.symbol, t.action, t.shares) for t in plan.trades] == [("AAPL", "sell", 10.0)]


def test_an_unpriced_new_symbol_is_planned_by_value() -> None:
    holdings = {"XOM": (10.0, 100.0)}
    targets = {"XOM": Target(0.5, "market", None, ""), "ZZZZ": Target(0.5, "market", None, "")}

    plan = plan_trades(holdings, targets, {"ZZZZ": None}, cash=1_000.0)

    unpriced = next(t for t in plan.trades if t.symbol == "ZZZZ")
    assert unpriced.shares is None and unpriced.value == pytest.approx(1_000.0)
    assert any("ZZZZ" in note for note in plan.notes)


# Routes.


@pytest.fixture
def market(monkeypatch: pytest.MonkeyPatch) -> list[PortfolioContext]:
    """Fake prices and Claude; return the contexts Claude was asked about."""

    async def info(symbol: str, client: Any = None) -> TickerInfo:
        if symbol == "SPAXX":
            return TickerInfo(symbol=symbol, price=1.0, quote_type="MONEYMARKET", source="test")
        return TickerInfo(symbol=symbol, price=PRICES[symbol], sector=SECTORS[symbol], source="fmp")

    async def closes(
        symbols: list[str], period: str, interval: str, tolerate_failures: bool = False
    ) -> ClosePanel:
        prices = 100 * (1 + _returns()[symbols]).cumprod()
        return ClosePanel(closes=prices, timezones={s: "America/New_York" for s in symbols})

    asked: list[PortfolioContext] = []

    async def write(context: PortfolioContext, *, client: Any) -> tuple[PortfolioReview, str]:
        asked.append(context)
        return REVIEW, "claude-opus-5-5"

    monkeypatch.setattr(portfolio, "fetch_ticker_info", info)
    monkeypatch.setattr(portfolio, "fetch_close_panel", closes)
    monkeypatch.setattr(portfolio, "write_portfolio_review", write)
    return asked


def _save(holdings: dict[str, float], headers: dict[str, str] | None = None) -> None:
    body = {"holdings": [{"symbol": s, "shares": n} for s, n in holdings.items()]}
    response = client.put("/api/v1/portfolio/holdings", headers=headers, json=body)
    assert response.status_code == 200


def test_review_is_grounded_in_the_saved_holdings(market: list[PortfolioContext]) -> None:
    _save({"AAPL": 10, "MSFT": 5, "XOM": 20})

    response = client.post("/api/v1/portfolio/analysis", json={"period": "1y"})

    assert response.status_code == 200
    body = response.json()
    assert body["report"]["headline"] == REVIEW.headline
    assert body["cached"] is False and body["id"]
    context = market[0]
    assert {h.symbol: round(h.weight, 4) for h in context.holdings} == {
        "AAPL": 0.3333,
        "MSFT": 0.3333,
        "XOM": 0.3333,
    }
    assert context.sectors == pytest.approx({"Technology": 2 / 3, "Energy": 1 / 3})
    assert context.total_value == pytest.approx(6000.0)
    # 2,000 in each holding of 6,000: to 1,500, 1,500, 1,800 and 1,200 of VXUS.
    trades = {t["symbol"]: (t["action"], t["shares"]) for t in body["plan"]["trades"]}
    assert trades == {
        "AAPL": ("sell", 2),
        "MSFT": ("sell", 1),
        "XOM": ("sell", 2),
        "VXUS": ("buy", 24),
    }
    vxus = next(t for t in body["plan"]["trades"] if t["symbol"] == "VXUS")
    assert (vxus["order_type"], vxus["limit_price"]) == ("limit", 49.75)


def test_the_profile_reaches_claude_and_sizes_the_plan(market: list[PortfolioContext]) -> None:
    _save({"AAPL": 10, "MSFT": 5, "XOM": 20})
    profile = {"risk_tolerance": "conservative", "horizon_years": 5, "cash_to_invest": 4000}

    body = client.post("/api/v1/portfolio/analysis", json={"profile": profile}).json()
    again = client.post(
        "/api/v1/portfolio/analysis", json={"profile": {**profile, "notes": "Taxable account."}}
    ).json()

    assert market[0].profile.risk_tolerance == "conservative"
    assert body["plan"]["total_value"] == pytest.approx(10_000.0)
    assert again["cached"] is False, "a different profile is a different review"
    assert market[1].profile.notes == "Taxable account."


def test_a_recent_review_of_the_same_holdings_is_reused(
    market: list[PortfolioContext],
) -> None:
    _save({"AAPL": 10, "XOM": 20})
    first = client.post("/api/v1/portfolio/analysis").json()

    again = client.post("/api/v1/portfolio/analysis").json()
    latest = client.get("/api/v1/portfolio/analysis").json()
    _save({"AAPL": 11, "XOM": 20})
    changed = client.post("/api/v1/portfolio/analysis").json()

    assert again["cached"] is True and again["id"] == first["id"]
    assert latest["id"] == first["id"]
    assert changed["cached"] is False
    assert len(market) == 2


def test_the_second_portfolio_is_reviewed_with_its_cash(
    market: list[PortfolioContext],
) -> None:
    _save({"AAPL": 10})
    body = {"holdings": [{"symbol": "XOM", "shares": 20}, {"symbol": "SPAXX", "shares": 2000}]}
    client.put("/api/v1/portfolio/holdings", params={"portfolio": 2}, json=body)

    second = client.post("/api/v1/portfolio/analysis", json={"portfolio": 2}).json()
    first = client.get("/api/v1/portfolio/analysis")
    latest = client.get("/api/v1/portfolio/analysis", params={"portfolio": 2}).json()

    context = market[0]
    assert {h.symbol: (round(h.weight, 6), h.cash) for h in context.holdings} == {
        "XOM": (0.5, False),
        "SPAXX": (0.5, True),
    }
    assert context.sectors == pytest.approx({"Energy": 0.5, "Cash": 0.5})
    assert context.notice is not None and "Counted as cash: SPAXX" in context.notice
    assert first.status_code == 404
    assert latest["id"] == second["id"]


def test_review_needs_saved_holdings(market: list[PortfolioContext]) -> None:
    response = client.post("/api/v1/portfolio/analysis")

    assert response.status_code == 422
    assert "Save your holdings" in response.json()["detail"]
    assert client.get("/api/v1/portfolio/analysis").status_code == 404


@pytest.mark.usefixtures("real_auth")
def test_reviews_count_against_the_ai_limits(market: list[PortfolioContext], user: User) -> None:
    headers = _signed_in(user)
    _save({"AAPL": 10, "XOM": 20}, headers)
    client.put("/api/v1/me/limits", headers=headers, json={"ai_requests_per_hour": 1})

    client.post("/api/v1/portfolio/analysis", headers=headers, json={"period": "1y"})
    over = client.post("/api/v1/portfolio/analysis", headers=headers, json={"period": "2y"})

    async def usage() -> int:
        async with get_sessionmaker()() as session:
            return (await ai_usage(session, user.id)).last_hour

    assert over.status_code == 429
    assert asyncio.run(usage()) == 1


def test_review_streams_context_fragments_and_result(
    market: list[PortfolioContext], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def stream(context: PortfolioContext, *, client: Any) -> AsyncIterator[object]:
        yield AnalysisDelta(channel="thinking", text="Weighing sectors.")
        yield Written(output=REVIEW, model="claude-opus-5-5")

    monkeypatch.setattr(portfolio, "stream_portfolio_review", stream)
    _save({"AAPL": 10, "XOM": 20})

    events = _sse_events(client.post("/api/v1/portfolio/analysis/stream").text)

    assert [name for name, _ in events] == ["context", "thinking", "result"]
    assert events[2][1]["report"]["headline"] == REVIEW.headline  # type: ignore[index]


def test_review_refusals_are_422(
    market: list[PortfolioContext], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(context: PortfolioContext, *, client: Any) -> tuple[PortfolioReview, str]:
        raise AIRefusalError("declined")

    monkeypatch.setattr(portfolio, "write_portfolio_review", refuse)
    _save({"AAPL": 10, "XOM": 20})

    assert client.post("/api/v1/portfolio/analysis").status_code == 422
