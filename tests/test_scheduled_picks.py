"""Claude's picks made on a schedule, the screener refresh's schedule, and the picks shown
holdings or left out of reviews."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.security import decrypt_secret
from app.db import scheduled_picks as storage
from app.db.nightly import RUNNING, start_run
from app.db.session import get_sessionmaker
from app.db.tables import NightlyRefreshRecord, ScheduledPicksRecord
from app.db.users import User
from app.jobs import nightly as job
from app.main import app
from app.models.picks import PicksRequest
from tests.test_nightly_refresh import (  # noqa: F401 (encryption is a fixture)
    ANTHROPIC,
    FINNHUB,
    _headers,
    _key_for,
    encryption,
)  # noqa: F401

client = TestClient(app)
pytestmark = pytest.mark.usefixtures("real_auth", "encryption")
URL = "/api/v1/me/picks-schedule"


@pytest.fixture
def key(user: User) -> str:
    return _key_for(user)


def _stored(user: User) -> ScheduledPicksRecord | None:
    async def read() -> ScheduledPicksRecord | None:
        async with get_sessionmaker()() as session:
            return await session.get(ScheduledPicksRecord, user.id)

    return asyncio.run(read())


def _set(user: User, **values: Any) -> None:
    async def write() -> None:
        async with get_sessionmaker()() as session:
            record = await session.get(ScheduledPicksRecord, user.id)
            assert record is not None
            for name, value in values.items():
                setattr(record, name, value)
            await session.commit()

    asyncio.run(write())


SETTINGS = {
    "weekly": {"weekday": 6, "time": "09:00"},
    "monthly": {"month_day": -1, "time": "09:00"},
    "time_zone": "America/New_York",
    "request": {"profile": {"risk_tolerance": "moderate", "notes": "no banks"}},
}


def test_scheduling_stores_the_keys_encrypted_and_says_when_each_runs(user: User, key: str) -> None:
    response = client.put(
        URL, headers=_headers(key, anthropic=ANTHROPIC, finnhub=FINNHUB), json=SETTINGS
    )

    assert response.status_code == 200, response.text
    status = response.json()
    assert status["enabled"] and status["weekly"]["weekday"] == 6
    assert status["next_weekly_at"] and status["next_monthly_at"]
    assert status["request"]["profile"]["notes"] == "no banks"
    record = _stored(user)
    assert record is not None and ANTHROPIC not in record.credentials
    assert json.loads(decrypt_secret(record.credentials).get_secret_value()) == {
        "anthropic": ANTHROPIC,
        "finnhub": FINNHUB,
    }

    assert client.delete(URL, headers=_headers(key)).json()["enabled"] is False
    assert _stored(user) is None, "turning it off deletes the keys"


def test_scheduling_needs_an_anthropic_key_and_a_schedule(key: str) -> None:
    without_key = client.put(URL, headers=_headers(key, finnhub=FINNHUB), json=SETTINGS)
    nothing = client.put(
        URL, headers=_headers(key, anthropic=ANTHROPIC), json={"time_zone": "America/New_York"}
    )

    assert without_key.status_code == 422 and "Anthropic" in without_key.json()["detail"]
    assert nothing.status_code == 422


def test_due_picks_wait_for_a_running_screener_refresh(user: User, key: str) -> None:
    # Mondays, and the last day of the month: Monday, November 30, 2026 is both.
    monday = SETTINGS | {"weekly": {"weekday": 0, "time": "09:00"}}
    client.put(URL, headers=_headers(key, anthropic=ANTHROPIC), json=monday)
    now = datetime(2026, 11, 30, 15, 0, tzinfo=timezone.utc)  # 10:00 in New York
    long_ago = now - timedelta(days=40)
    _set(user, weekly_ran_at=long_ago, monthly_ran_at=long_ago)

    async def due() -> list[storage.DuePicks]:
        async with get_sessionmaker()() as session:
            return await storage.due_picks(session, now)

    [found] = asyncio.run(due())
    assert found.which == ["weekly", "monthly"], "both due run together"
    assert found.keys.anthropic is not None

    async def start_refresh() -> None:
        async with get_sessionmaker()() as session:
            session.add(
                NightlyRefreshRecord(
                    user_id=user.id,
                    refresh_time="19:00",
                    time_zone="America/New_York",
                    credentials="x",
                    providers=["finnhub"],
                    created_at=now,
                    keys_updated_at=now,
                )
            )
            await session.commit()
            await start_run(session, user.id, now)

    asyncio.run(start_refresh())
    assert asyncio.run(due()) == [], "they wait while the refresh runs"


def test_a_scheduled_run_makes_each_horizons_picks_and_records_failures(
    user: User, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.put(URL, headers=_headers(key, anthropic=ANTHROPIC), json=SETTINGS)
    made: list[PicksRequest] = []

    async def make(user_id: str, request: PicksRequest, api_key: Any) -> None:
        made.append(request)
        if request.profile.horizon.months:
            raise RuntimeError("Claude declined")

    monkeypatch.setattr(job, "make_scheduled_picks", make)
    record = _stored(user)
    assert record is not None
    due = storage.DuePicks(
        user_id=user.id,
        which=["weekly", "monthly"],
        keys=storage._keys(record),
        request=PicksRequest.model_validate(record.request),
    )

    asyncio.run(job.run_scheduled_picks(due))

    assert [r.profile.horizon.label for r in made] == ["1 week", "1 month"]
    assert all(r.profile.notes == "no banks" for r in made), "the tailoring is kept"
    after = _stored(user)
    assert after is not None and after.running_since is None
    assert after.last_error is not None and "monthly" in after.last_error


def test_the_screener_refresh_follows_its_schedule(key: str) -> None:
    response = client.put(
        "/api/v1/me/nightly-refresh",
        headers=_headers(key, finnhub=FINNHUB),
        json={
            "refresh_time": "21:00",
            "schedule": {"frequency": "trading_days", "times": {"4": "23:00"}},
        },
    )

    assert response.status_code == 200, response.text
    status = response.json()
    assert status["schedule"]["frequency"] == "trading_days"
    assert status["schedule"]["times"] == {"4": "23:00"}
    moment = datetime.fromisoformat(status["next_run_at"])
    from app.stats.schedule import is_trading_day
    from zoneinfo import ZoneInfo

    assert is_trading_day(moment.astimezone(ZoneInfo("America/New_York")).date())
    assert RUNNING == "running"


def test_a_picks_time_inside_the_refresh_is_flagged_and_a_clear_one_is_not(key: str) -> None:
    client.put(
        "/api/v1/me/nightly-refresh",
        headers=_headers(key, finnhub=FINNHUB),
        json={"refresh_time": "08:00", "time_zone": "America/New_York"},
    )
    # After each NYSE session, at 08:00: Saturday's run takes Friday's close, and on Sunday
    # none runs, since no session closed since Saturday's.
    clash = SETTINGS | {"weekly": {"weekday": 5, "time": "09:00"}, "monthly": None}
    clear = SETTINGS | {"weekly": {"weekday": 6, "time": "09:00"}, "monthly": None}

    flagged = client.post(
        "/api/v1/me/schedule-conflicts", headers=_headers(key), json={"picks": clash}
    ).json()["warnings"]
    fine = client.post(
        "/api/v1/me/schedule-conflicts", headers=_headers(key), json={"picks": clear}
    ).json()["warnings"]

    assert flagged and "weekly picks on Saturday" in flagged[0] and "8:00 AM" in flagged[0]
    assert fine == []
