"""Tests for Claude's picks: the candidates, the routes, storage, the screener's filter,
and the picks a portfolio review is given.

Claude and the market calendar are faked; the screener is a small stored universe.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai import web
from app.ai.agent import AIRefusalError, Written
from app.ai.picks import candidates_table, picks_messages, picks_research_task
from app.ai.prompts import PICKS_SYSTEM_PROMPT, PORTFOLIO_SYSTEM_PROMPT
from app.api.v1.endpoints import picks as picks_route
from app.api.v1.endpoints.picks import TooFewPicksError, settle_picks
from app.data.fetcher import ProviderNotConfiguredError
from app.db.screener import finish_run, replace_universe, start_run
from app.db.session import get_sessionmaker
from app.db.users import User, ai_usage
from app.main import app
from app.models.markets import MarketContext
from app.models.picks import Horizon, Pick, PicksContext, PicksProfile, PicksReport
from app.models.portfolio_analysis import PortfolioContext
from app.models.research import AnalysisDelta
from app.models.screener import RankedStock, ScreenedStock
from app.models.web import WebSearchQuery
from app.stats.picks import LENSES, lenses_for, merge_candidates
from tests.test_api import _sse_events
from tests.test_portfolio_review import _save, _signed_in, market  # noqa: F401
from tests.test_screener_api import _stock
from tests.test_web_research import RESEARCH

client = TestClient(app)

PICKS = "/api/v1/picks"

STOCKS = [
    _stock("BIG", 1, pe_ratio=30.0, momentum_12_1=0.4, volatility=0.3, sector="Technology"),
    _stock("CHEAP", 2, pe_ratio=9.0, fcf_yield=0.09, volatility=0.18, sector="Finance"),
    _stock("GROW", 3, revenue_growth=0.6, return_1m=0.12, volatility=0.5, sector="Technology"),
    _stock("STEADY", 4, return_on_equity=0.3, volatility=0.12, sector="Consumer Staples"),
    _stock("THIN", 600, pe_ratio=5.0, momentum_12_1=2.0, avg_dollar_volume=1e6),
    _stock("TINY", 700, market_cap=5e7, momentum_12_1=3.0),
    ScreenedStock(
        symbol="MKT",
        name="Whole Market ETF",
        exchange="NYSE",
        asset_type="etf",
        market_cap_source="provider",
        price=400.0,
        avg_dollar_volume=5e9,
        volatility=0.15,
        momentum_12_1=0.2,
    ),
]


def _pick(symbol: str, weight: float, kind: str = "stock", **fields: Any) -> Pick:
    values: dict[str, Any] = {
        "symbol": symbol,
        "name": f"{symbol} Inc.",
        "kind": kind,
        "conviction": "medium",
        "weight": weight,
        "thesis": f"{symbol} leads its lens.",
        "risk": "It could fall.",
        "entry": "Buy at market.",
        "exit": "Look again at the end of the period.",
    }
    return Pick(**(values | fields))


REPORT = PicksReport(
    headline="Quality at a fair price, with momentum.",
    market_view="Leadership is narrow.",
    approach="Weighted by conviction for a one-year hold.",
    picks=[
        _pick("big", 0.5, conviction="high"),
        _pick("CHEAP", 0.25),
        _pick("STEADY", 0.15),
        _pick("VTI", 0.1, kind="etf"),
    ],
    passed_over=["GROW: too volatile for this profile."],
    considerations=["Taxes on short-term gains."],
    data_limitations=["No news."],
)


def _store(stocks: list[ScreenedStock]) -> None:
    async def run() -> None:
        async with get_sessionmaker()() as session:
            await replace_universe(session, stocks)
            run_id = await start_run(session)
            await finish_run(session, run_id, error=None, stocks=len(stocks))

    asyncio.run(run())


@pytest.fixture
def screener(monkeypatch: pytest.MonkeyPatch) -> list[PicksContext]:
    """Store the universe, fake the market calendar and Claude; return the contexts Claude
    was asked about."""
    _store(STOCKS)
    asked: list[PicksContext] = []

    async def no_calendar(today: object) -> MarketContext:
        raise ProviderNotConfiguredError("No Finnhub key.")

    async def write(context: PicksContext, *, client: Any) -> tuple[PicksReport, str]:
        asked.append(context)
        return REPORT, "claude-opus-5-5"

    monkeypatch.setattr(picks_route, "fetch_market_context", no_calendar)
    monkeypatch.setattr(picks_route, "write_picks", write)
    return asked


# The holding period.


def test_a_horizon_is_any_mix_of_years_months_and_weeks() -> None:
    assert Horizon(weeks=1).label == "1 week"
    assert Horizon(months=6).label == "6 months"
    assert Horizon(years=1, weeks=2).label == "1 year and 2 weeks"
    assert Horizon(years=2, months=6, weeks=3).label == "2 years, 6 months, and 3 weeks"
    assert Horizon(years=1).total_weeks == pytest.approx(52.18)
    assert Horizon(months=3, weeks=1).total_weeks == pytest.approx(52.18 / 4 + 1)


@pytest.mark.parametrize("horizon", [{}, {"weeks": 0}, {"years": 61}, {"weeks": -1}])
def test_a_horizon_runs_from_one_week_to_sixty_years(horizon: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        Horizon(**horizon)


# The candidates.


def test_lenses_cover_the_kinds_asked_for() -> None:
    assert {lens.asset_type for lens in lenses_for([])} == {"stock", "etf"}
    assert {lens.asset_type for lens in lenses_for(["etf"])} == {"etf"}
    assert all(lens.request().asset_types == [lens.asset_type] for lens in LENSES)
    stock = next(lens for lens in LENSES if lens.name == "12-month momentum").request()
    assert {f.field for f in stock.filters} == {"avg_dollar_volume", "price", "market_cap"}
    etf = next(lens for lens in LENSES if lens.asset_type == "etf").request()
    assert "market_cap" not in {f.field for f in etf.filters}, "an ETF has no market cap"


def test_candidates_are_each_listed_once_with_every_lens_they_led() -> None:
    def ranked(stock: ScreenedStock) -> RankedStock:
        return RankedStock(**stock.model_dump(), rank=1)

    first, second = LENSES[0], LENSES[1]

    merged = merge_candidates(
        [(first, [ranked(STOCKS[0]), ranked(STOCKS[1])]), (second, [ranked(STOCKS[0])])]
    )

    assert [(c.symbol, c.lenses) for c in merged] == [
        ("BIG", [first.name, second.name]),
        ("CHEAP", [first.name]),
    ]
    assert merged[0].pe_ratio == 30.0 and merged[0].sector == "Technology"


def test_the_candidates_go_to_claude_as_a_table(screener: list[PicksContext]) -> None:
    client.post(PICKS, json={})

    context = screener[0]
    table = candidates_table(context.candidates).splitlines()
    assert table[0].startswith("symbol,name,asset_type,sector,industry,lenses,price,market_cap")
    big = next(line for line in table if line.startswith("BIG,"))
    assert "largest companies; 12-month momentum" in big and ",0.4," in big
    message = cast(str, picks_messages(context)[0]["content"])
    assert "<candidates>" in message and '"candidates"' not in message
    assert '"horizon": "1 year"' in message


def test_only_liquid_securities_large_enough_are_candidates(
    screener: list[PicksContext],
) -> None:
    client.post(PICKS, json={})

    symbols = {candidate.symbol for candidate in screener[0].candidates}
    assert symbols == {"BIG", "CHEAP", "GROW", "STEADY", "MKT"}, "THIN and TINY are left out"
    fund = next(c for c in screener[0].candidates if c.symbol == "MKT")
    assert fund.asset_type == "etf" and "most traded ETFs" in fund.lenses


def test_the_kinds_asked_for_bound_the_candidates(screener: list[PicksContext]) -> None:
    client.post(PICKS, json={"profile": {"asset_types": ["stock"]}})
    client.post(PICKS, json={"profile": {"asset_types": ["etf"]}})

    assert "MKT" not in {c.symbol for c in screener[0].candidates}
    assert {c.symbol for c in screener[1].candidates} == {"MKT"}


# Settling an answer.


def test_picks_are_tidied_and_their_weights_scaled_to_one() -> None:
    report = REPORT.model_copy(
        update={
            "picks": [
                _pick(" aapl ", 0.2),
                _pick("AAPL", 0.5),
                _pick("not a ticker!", 0.1),
                _pick("MSFT", 0.2),
                _pick("XOM", 0.1),
            ]
        }
    )

    settled = settle_picks(report)

    assert [p.symbol for p in settled.picks] == ["AAPL", "MSFT", "XOM"]
    assert [p.weight for p in settled.picks] == pytest.approx([0.4, 0.4, 0.2])


def test_unweighted_picks_are_weighted_equally_and_too_few_are_refused() -> None:
    equal = settle_picks(
        REPORT.model_copy(update={"picks": [_pick(s, 0.0) for s in ("A", "B", "C", "D")]})
    )
    assert [p.weight for p in equal.picks] == [0.25] * 4

    with pytest.raises(TooFewPicksError):
        settle_picks(REPORT.model_copy(update={"picks": [_pick("A", 1.0), _pick("B", 1.0)]}))


# The routes.


def test_picks_come_with_the_screeners_figures_and_are_stored(
    screener: list[PicksContext], user: User
) -> None:
    profile = {
        "horizon": {"months": 6, "weeks": 2},
        "risk_tolerance": "aggressive",
        "amount": 10000,
        "notes": "No tobacco.",
    }

    response = client.post(PICKS, json={"profile": profile})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cached"] is False and body["id"]
    assert [p["symbol"] for p in body["report"]["picks"]] == ["BIG", "CHEAP", "STEADY", "VTI"]
    assert sum(p["weight"] for p in body["report"]["picks"]) == pytest.approx(1.0)
    # VTI is not in the screener, so it has no figures; the rest carry theirs.
    assert [s["symbol"] for s in body["securities"]] == ["BIG", "CHEAP", "STEADY"]
    assert body["securities"][1]["fcf_yield"] == 0.09
    context = screener[0]
    assert context.horizon == "6 months and 2 weeks"
    assert context.horizon_weeks == pytest.approx(28.1, abs=0.1)
    assert context.profile.risk_tolerance == "aggressive" and context.profile.amount == 10000
    assert context.universe.securities == len(STOCKS) and context.universe.has_etfs
    assert context.market is None and context.notice is not None

    latest = client.get(f"{PICKS}/latest").json()
    listed = client.get(PICKS).json()["items"]
    one = client.get(f"{PICKS}/{body['id']}").json()

    assert latest["id"] == body["id"] == one["id"] and latest["cached"] is True
    assert listed == [
        {
            "id": body["id"],
            "generated_at": listed[0]["generated_at"],
            "horizon": "6 months and 2 weeks",
            "risk_tolerance": "aggressive",
            "model": "claude-opus-5-5",
            "headline": REPORT.headline,
            "symbols": ["BIG", "CHEAP", "STEADY", "VTI"],
        }
    ]

    async def usage() -> int:
        async with get_sessionmaker()() as session:
            return (await ai_usage(session, user.id)).last_hour

    assert asyncio.run(usage()) == 1, "picks count against the AI limits"


def test_recent_picks_for_the_same_profile_and_screener_are_reused(
    screener: list[PicksContext],
) -> None:
    first = client.post(PICKS, json={}).json()
    again = client.post(PICKS, json={}).json()
    other = client.post(PICKS, json={"profile": {"horizon": {"weeks": 1}}}).json()
    fresh = client.post(PICKS, json={"refresh": True}).json()
    _store(STOCKS)  # A refreshed screener is new data to pick from.
    refreshed = client.post(PICKS, json={}).json()

    assert again["cached"] is True and again["id"] == first["id"]
    assert other["cached"] is False and fresh["cached"] is False
    assert refreshed["cached"] is False
    assert len(screener) == 4


def test_picks_need_a_screener() -> None:
    response = client.post(PICKS, json={})

    assert response.status_code == 503, "no universe has been built"
    assert "nightly screener refresh" in response.json()["detail"]
    assert client.get(f"{PICKS}/latest").status_code == 404


def test_picks_need_an_anthropic_key(
    screener: list[PicksContext], user: User, real_auth: None
) -> None:
    headers = _signed_in(user)
    del headers["X-Anthropic-Key"]

    response = client.post(PICKS, json={}, headers=headers)

    assert response.status_code == 403 and len(screener) == 0


def test_a_screener_with_nothing_liquid_enough_is_422(screener: list[PicksContext]) -> None:
    _store([STOCKS[4], STOCKS[5]])

    response = client.post(PICKS, json={"refresh": True})

    assert response.status_code == 422 and "nothing to pick from" in response.json()["detail"]


def test_an_answer_with_too_few_picks_is_not_stored(
    screener: list[PicksContext], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def write(context: PicksContext, *, client: Any) -> tuple[PicksReport, str]:
        return REPORT.model_copy(update={"picks": REPORT.picks[:1]}), "claude-opus-5-5"

    monkeypatch.setattr(picks_route, "write_picks", write)

    assert client.post(PICKS, json={}).status_code == 502
    assert client.get(f"{PICKS}/latest").status_code == 404


def test_refusals_are_422(screener: list[PicksContext], monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(context: PicksContext, *, client: Any) -> tuple[PicksReport, str]:
        raise AIRefusalError("The AI model declined.")

    monkeypatch.setattr(picks_route, "write_picks", refuse)

    assert client.post(PICKS, json={}).status_code == 422


def test_picks_stream_with_each_web_search(
    screener: list[PicksContext], monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[PicksContext] = []
    tasks: list[str] = []

    async def stream(context: PicksContext, *, client: Any) -> AsyncIterator[object]:
        written.append(context)
        yield AnalysisDelta(channel="report", text='{"headline"')
        yield Written(output=REPORT, model="claude-opus-5-5")

    async def found(task: str, *, client: Any) -> AsyncIterator[object]:
        tasks.append(task)
        yield WebSearchQuery(query="market outlook")
        yield RESEARCH

    monkeypatch.setattr(picks_route, "stream_picks", stream)
    monkeypatch.setattr(web, "stream_web_research", found)

    events = _sse_events(client.post(f"{PICKS}/stream", json={"web_search": True}).text)
    reused = _sse_events(client.post(f"{PICKS}/stream", json={"web_search": True}).text)

    assert [name for name, _ in events] == ["context", "search", "report", "result"]
    result = cast(dict[str, Any], events[3][1])
    assert result["context"]["web_research"]["findings"][0]["id"] == 1
    assert written[0].web_research == RESEARCH
    assert "hold for 1 year" in tasks[0] and "BIG," in tasks[0]
    assert [name for name, _ in reused] == ["context", "result"]


def test_stored_picks_are_deleted_and_kept_apart_by_account(screener: list[PicksContext]) -> None:
    body = client.post(PICKS, json={}).json()
    missing = "00000000-0000-0000-0000-000000000000"

    assert client.get(f"{PICKS}/{missing}").status_code == 404
    assert client.delete(f"{PICKS}/{missing}").status_code == 404
    assert client.delete(f"{PICKS}/{body['id']}").status_code == 204
    assert client.get(PICKS).json()["items"] == []


def test_the_prompt_can_be_taken_outside_and_the_answer_brought_back(
    screener: list[PicksContext],
) -> None:
    exported = client.post(f"{PICKS}/prompt", json={"profile": {"horizon": {"years": 3}}})
    assert exported.status_code == 200, exported.text
    prompt = exported.json()
    assert prompt["filename"] == "pyqxel-claudes-picks-3-years-prompt.md"
    assert PICKS_SYSTEM_PROMPT[:60] in prompt["prompt"] and "BIG," in prompt["prompt"]

    saved = client.post(
        f"{PICKS}/import",
        json={
            "request": {"profile": {"horizon": {"years": 3}}},
            "context": prompt["context"],
            "response": f"Here you go:\n```json\n{REPORT.model_dump_json()}\n```",
        },
    )
    bad = client.post(f"{PICKS}/import", json={"context": prompt["context"], "response": "{}"})

    assert saved.status_code == 200, saved.text
    assert saved.json()["model"] == "claude (pasted)"
    assert client.get(f"{PICKS}/latest").json()["id"] == saved.json()["id"]
    assert bad.status_code == 422
    assert len(screener) == 0, "nothing was sent to Claude"


def test_the_research_task_states_the_profile() -> None:
    context = PicksContext(
        profile=PicksProfile(horizon=Horizon(weeks=2), risk_tolerance="aggressive", notes="AI."),
        horizon="2 weeks",
        horizon_weeks=2,
        today="2026-11-13",  # type: ignore[arg-type]
        universe={"securities": 1, "own": True, "has_etfs": False},  # type: ignore[arg-type]
        candidates=[],
    )

    task = picks_research_task(context)

    assert "hold for 2 weeks, for an investor whose risk tolerance is aggressive" in task
    assert "The person added: AI." in task


def test_claude_is_told_not_to_hide_in_the_index() -> None:
    """Broad ETFs are allowed when they are the best answer, never as a default."""
    assert "do not hide in the index" in PICKS_SYSTEM_PROMPT
    assert "Never fill the list with index funds" in PICKS_SYSTEM_PROMPT
    assert "when it is, pick it" in PICKS_SYSTEM_PROMPT


# The screener's filter.


def test_the_screener_can_show_the_picks_alone(screener: list[PicksContext]) -> None:
    picked = client.post(PICKS, json={}).json()
    symbols = [pick["symbol"] for pick in picked["report"]["picks"]]

    screened = client.post(
        "/api/v1/screener", json={"universe": "all", "symbols": symbols + ["  mkt "]}
    ).json()

    assert sorted(item["symbol"] for item in screened["items"]) == [
        "BIG",
        "CHEAP",
        "MKT",
        "STEADY",
    ], "VTI is not in the screener, so it matches nothing"
    assert screened["total"] == 4
    everything = client.post("/api/v1/screener", json={"universe": "all"}).json()
    assert everything["total"] == len(STOCKS)


# The portfolio review.


def test_a_review_is_given_the_latest_picks_with_their_figures(
    screener: list[PicksContext], market: list[PortfolioContext]  # noqa: F811
) -> None:
    _save({"AAPL": 10, "MSFT": 5, "XOM": 20})
    before = client.post("/api/v1/portfolio/analysis", json={}).json()
    picked = client.post(PICKS, json={"profile": {"risk_tolerance": "moderate"}}).json()

    after = client.post("/api/v1/portfolio/analysis", json={}).json()
    again = client.post("/api/v1/portfolio/analysis", json={}).json()

    assert before["context"]["claude_picks"] is None
    assert after["cached"] is False, "new picks are new data for the review"
    assert again["cached"] is True
    given = market[1].claude_picks
    assert given is not None
    assert given.horizon == "1 year" and given.profile.risk_tolerance == "moderate"
    assert given.headline == REPORT.headline and given.market_view == REPORT.market_view
    assert [p.symbol for p in given.picks] == ["BIG", "CHEAP", "STEADY", "VTI"]
    cheap = given.picks[1]
    assert cheap.thesis == "CHEAP leads its lens." and cheap.weight == pytest.approx(0.25)
    assert cheap.metrics is not None and cheap.metrics.fcf_yield == 0.09
    assert given.picks[3].metrics is None, "VTI is not in the screener"
    assert after["context"]["claude_picks"]["picks"][0]["metrics"]["pe_ratio"] == 30.0
    assert picked["id"]
    assert "claude_picks" in PORTFOLIO_SYSTEM_PROMPT


def test_the_review_prompt_holds_the_picks(
    screener: list[PicksContext], market: list[PortfolioContext]  # noqa: F811
) -> None:
    _save({"AAPL": 10, "XOM": 20})
    client.post(PICKS, json={})

    exported = client.post("/api/v1/portfolio/analysis/prompt", json={}).json()

    assert '"claude_picks"' in exported["prompt"] and "CHEAP leads its lens." in exported["prompt"]
