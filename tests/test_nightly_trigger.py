"""Cloud Scheduler's hourly call, which starts the nightly refresh job only when a user's
refresh is due, and only for the scheduler's own account."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api import internal
from app.core.cloud_run import caller_matches
from app.core.config import get_settings
from app.db import nightly as storage
from app.db.session import get_sessionmaker
from app.db.tables import NightlyRefreshRecord
from app.db.users import User
from app.main import app

client = TestClient(app)

JOB = "projects/p/locations/us-central1/jobs/pyqxel-nightly"
CALLER = "pyqxel-scheduler@p.iam.gserviceaccount.com"
URL = "/internal/nightly-refresh"
SIGNED = {"Authorization": "Bearer signed-by-google"}


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A server with a nightly job, whose caller check accepts only the token in
    :data:`SIGNED`, and which records the jobs it starts instead of starting them."""
    monkeypatch.setattr(get_settings(), "nightly_job", JOB)
    monkeypatch.setattr(get_settings(), "nightly_trigger_caller", CALLER)
    runs: list[str] = []

    async def verify(token: str, audience: str, email: str) -> bool:
        return (token, audience, email) == ("signed-by-google", "pyqxel-nightly-trigger", CALLER)

    async def run(name: str) -> None:
        runs.append(name)

    monkeypatch.setattr(internal, "verify_caller", verify)
    monkeypatch.setattr(internal, "run_job", run)
    return runs


def _opt_in(user: User, refresh_time: str) -> None:
    async def add() -> None:
        now = datetime.now(timezone.utc)
        async with get_sessionmaker()() as session:
            session.add(
                NightlyRefreshRecord(
                    user_id=user.id,
                    refresh_time=refresh_time,
                    time_zone="UTC",
                    credentials="unread here",
                    providers=["finnhub"],
                    created_at=now,
                    keys_updated_at=now,
                )
            )
            await session.commit()

    asyncio.run(add())


def _start_run(user: User, when: datetime) -> None:
    async def start() -> None:
        async with get_sessionmaker()() as session:
            await storage.start_run(session, user.id, when)

    asyncio.run(start())


def test_without_a_nightly_job_the_route_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "nightly_job", None)

    assert client.post(URL, headers=SIGNED).status_code == 404


def test_only_the_schedulers_token_is_accepted(started: list[str]) -> None:
    assert client.post(URL).status_code == 401
    assert client.post(URL, headers={"Authorization": "Bearer forged"}).status_code == 401
    assert started == []


def test_the_job_is_not_started_when_no_one_is_due(user: User, started: list[str]) -> None:
    response = client.post(URL, headers=SIGNED)
    assert response.status_code == 200 and response.json() == {"started": False}

    # Opted in, but already refreshed since today's time.
    _opt_in(user, "00:00")
    _start_run(user, datetime.now(timezone.utc))
    assert client.post(URL, headers=SIGNED).json() == {"started": False}
    assert started == []


def test_the_job_is_started_when_a_refresh_is_due(user: User, started: list[str]) -> None:
    _opt_in(user, "00:00")

    response = client.post(URL, headers=SIGNED)

    assert response.status_code == 200 and response.json() == {"started": True}
    assert started == [JOB]


def test_a_refresh_still_running_is_not_due_until_it_is_taken_to_have_stopped(
    user: User,
) -> None:
    _opt_in(user, "00:00")
    now = datetime(2026, 11, 9, 6, 0, tzinfo=timezone.utc)

    async def due_after_run_started(started_at: datetime) -> bool:
        async with get_sessionmaker()() as session:
            await storage.start_run(session, user.id, started_at)
            return await storage.any_due(session, now)

    # Both runs started yesterday, before today's time. One left running for longer than
    # STALE_AFTER is taken to have stopped, so the refresh is due.
    assert asyncio.run(due_after_run_started(now - storage.STALE_AFTER - timedelta(hours=1)))
    # One still running within it is not.
    assert not asyncio.run(due_after_run_started(now - storage.STALE_AFTER + timedelta(hours=1)))


def test_the_callers_token_must_name_its_account_and_audience_and_be_current() -> None:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "pyqxel-nightly-trigger",
        "email": CALLER,
        "email_verified": "true",
        "exp": "2000",
    }

    assert caller_matches(claims, "pyqxel-nightly-trigger", CALLER, now=1000)
    assert not caller_matches(claims, "pyqxel-nightly-trigger", CALLER, now=3000)
    assert not caller_matches(claims, "another-audience", CALLER, now=1000)
    assert not caller_matches(claims, "pyqxel-nightly-trigger", "someone@else", now=1000)
    assert not caller_matches(
        claims | {"email_verified": "false"}, "pyqxel-nightly-trigger", CALLER, now=1000
    )
    assert not caller_matches(
        claims | {"iss": "https://evil.example"}, "pyqxel-nightly-trigger", CALLER, now=1000
    )
    assert not caller_matches(claims | {"exp": "soon"}, "pyqxel-nightly-trigger", CALLER, now=1000)
