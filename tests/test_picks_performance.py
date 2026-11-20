"""Tests for how stored lists of Claude's picks have done: each pick's and the list's
gain since it was made, against a benchmark, until the holding period ends.

Prices are faked; the picks are stored through the picks routes with Claude faked.
"""

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import picks as picks_route
from app.data.fetcher import DataFetchError
from app.data.panel import ClosePanel
from app.main import app
from app.models.picks import PicksResponse
from app.stats.picks_performance import ends_on, history_period, start_day, track
from tests.test_picks import PICKS, REPORT, screener  # noqa: F401

client = TestClient(app)

DAYS = pd.to_datetime(["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"])
CLOSES = pd.DataFrame(
    {
        "BIG": [100.0, 110.0, 120.0, 130.0],
        "CHEAP": [50.0, 50.0, 45.0, 40.0],
        "STEADY": [10.0, 10.0, 10.0, 11.0],
        "SPY": [400.0, 404.0, 408.0, 412.0],
    },
    index=DAYS,
)


def _picks(generated_at: datetime, weeks: float = 52.18) -> PicksResponse:
    return PicksResponse.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "report": REPORT.model_copy(
                update={
                    "picks": [
                        p.model_copy(update={"symbol": p.symbol.upper()}) for p in REPORT.picks
                    ]
                }
            ).model_dump(),
            "context": {
                "profile": {},
                "horizon": "1 year",
                "horizon_weeks": weeks,
                "today": "2026-11-16",
                "universe": {"securities": 1, "own": False, "has_etfs": False},
                "candidates": [],
            },
            "model": "claude-opus-5-5",
            "generated_at": generated_at.isoformat(),
        }
    )


def test_a_list_is_measured_from_the_last_close_before_it_was_made() -> None:
    # 10 a.m. Eastern on the 17th: the 17th's close came later, so the 16th's is the start.
    assert start_day(datetime(2026, 11, 17, 15, tzinfo=timezone.utc)) == date(2026, 11, 16)
    # 6 p.m. Eastern: that day's close counts.
    assert start_day(datetime(2026, 11, 17, 23, tzinfo=timezone.utc)) == date(2026, 11, 17)


def test_each_pick_and_the_list_gain_since_the_start_against_the_benchmark() -> None:
    picks = _picks(datetime(2026, 11, 17, 15, tzinfo=timezone.utc))

    result = track(picks, CLOSES, "SPY", date(2026, 11, 19))

    gains = {p.symbol: p.gain for p in result.picks}
    assert gains["BIG"] == pytest.approx(0.3) and gains["CHEAP"] == pytest.approx(-0.2)
    assert gains["VTI"] is None, "no prices for it"
    # BIG 0.5, CHEAP 0.25, STEADY 0.15 of the 0.9 priced.
    expected = (0.5 * 0.3 + 0.25 * -0.2 + 0.15 * 0.1) / 0.9
    assert result.total_return == pytest.approx(expected, abs=1e-6)
    assert result.benchmark_return == pytest.approx(0.03)
    assert result.excess_return == pytest.approx(expected - 0.03, abs=1e-6)
    assert result.start == date(2026, 11, 16) and result.as_of == date(2026, 11, 19)
    assert result.series[0].picks == 0 and result.series[-1].picks == pytest.approx(
        expected, abs=1e-6
    )
    assert not result.ended and result.notice is not None and "VTI" in result.notice


def test_tracking_stops_when_the_holding_period_ends() -> None:
    picks = _picks(datetime(2026, 11, 16, 22, tzinfo=timezone.utc), weeks=2 / 7)  # two days

    result = track(picks, CLOSES, "SPY", date(2026, 11, 30))

    assert result.ended and result.ends_on == ends_on(picks) == date(2026, 11, 18)
    assert result.as_of == date(2026, 11, 18), "the 19th is after the period"
    assert {p.symbol: p.gain for p in result.picks}["BIG"] == pytest.approx(0.2)


def test_the_history_reaches_back_before_the_oldest_list() -> None:
    assert history_period(date(2026, 11, 1), date(2026, 11, 19)) == "1mo"
    assert history_period(date(2025, 1, 1), date(2026, 11, 19)) == "2y"
    assert history_period(date(2018, 1, 1), date(2026, 11, 19)) == "10y"


def _priced(monkeypatch: pytest.MonkeyPatch, asked: list[list[str]] | None = None) -> None:
    async def closes(
        symbols: list[str], period: str, interval: str, tolerate_failures: bool = False
    ) -> ClosePanel:
        if asked is not None:
            asked.append(symbols)
        kept = [s for s in symbols if s in CLOSES.columns]
        return ClosePanel(closes=CLOSES[kept], timezones={s: "America/New_York" for s in kept})

    monkeypatch.setattr(picks_route, "fetch_close_panel", closes)


def test_saved_picks_are_listed_with_how_they_did(
    screener: Any, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    made = client.post(PICKS, json={}).json()
    asked: list[list[str]] = []
    _priced(monkeypatch, asked)

    listed = client.get(f"{PICKS}/performance", params={"benchmark": "QQQ"})
    detail = client.get(f"{PICKS}/{made['id']}/performance")
    unknown = client.get(f"{PICKS}/00000000-0000-0000-0000-000000000009/performance")

    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["benchmark_name"] == "Nasdaq-100 (QQQ)"
    assert body["items"][0]["id"] == made["id"] and not body["items"][0]["ended"]
    assert body["items"][0]["symbols"] == ["BIG", "CHEAP", "STEADY", "VTI"]
    assert asked[0][-1] == "QQQ"
    assert detail.status_code == 200, detail.text
    assert detail.json()["benchmark"] == "SPY" and len(detail.json()["picks"]) == 4
    assert unknown.status_code == 404


def test_saved_picks_stay_listed_when_they_cannot_be_priced(
    screener: Any, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    client.post(PICKS, json={})

    async def down(*args: object, **kwargs: object) -> ClosePanel:
        raise DataFetchError("Every key refused.")

    monkeypatch.setattr(picks_route, "fetch_close_panel", down)
    listed = client.get(f"{PICKS}/performance").json()

    assert len(listed["items"]) == 1 and listed["items"][0]["total_return"] is None
    assert "could not be priced" in listed["notice"]
