"""Tests for accounts: authentication, credentials, isolation, limits, and who pays.

Requests here send real PyQxel API keys (the ``real_auth`` fixture). Providers are
faked, so nothing touches the network or spends money.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pandas as pd
import pytest
from anthropic import AsyncAnthropic
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.api import auth
from app.api.v1.endpoints import backtest, research, stocks
from app.core.config import Settings
from app.core.credentials import current_provider_keys
from app.data.fetcher import DataFetchError
from app.data.sec_edgar import FilingFetchError
from app.db.session import get_sessionmaker
from app.db.tables import UserRecord
from app.db.users import User, create_api_key
from app.jobs import users as users_cli
from app.main import app
from app.models.research import AnalysisContext, Filing, InvestmentThesis
from app.models.stock import TickerInfo
from tests.conftest import make_user

client = TestClient(app)

pytestmark = pytest.mark.usefixtures("real_auth")


def _key_for(user: User, name: str = "test") -> str:
    """Issue an API key for ``user``."""

    async def issue() -> str:
        async with get_sessionmaker()() as session:
            return (await create_api_key(session, user.id, name)).key

    return asyncio.run(issue())


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def alice(user: User) -> tuple[User, str]:
    """The test account and an API key for it."""
    return user, _key_for(user)


@pytest.fixture
def bob() -> tuple[User, str]:
    """A second account with its own Anthropic key and no FMP key."""
    other = asyncio.run(make_user("bob@example.com", "sk-ant-bob-key", None))
    return other, _key_for(other)


# Authentication.


def test_requests_without_a_key_are_refused() -> None:
    response = client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("key", ["pq_not-a-real-key", "Bearer", ""])
def test_unknown_keys_are_refused(key: str) -> None:
    assert client.get("/api/v1/me", headers=_headers(key)).status_code == 401


def test_keys_are_accepted_in_either_header(alice: tuple[User, str]) -> None:
    account, key = alice

    by_bearer = client.get("/api/v1/me", headers=_headers(key))
    by_header = client.get("/api/v1/me", headers={"X-API-Key": key})

    assert by_bearer.status_code == by_header.status_code == 200
    assert by_bearer.json()["email"] == account.email


def test_keys_in_the_url_are_refused_over_http(alice: tuple[User, str]) -> None:
    """Query-string keys end up in logs, so HTTP routes ignore them."""
    _, key = alice

    assert client.get(f"/api/v1/me?api_key={key}").status_code == 401


def test_revoked_keys_stop_working(alice: tuple[User, str]) -> None:
    account, key = alice
    listed = client.get("/api/v1/me/api-keys", headers=_headers(key)).json()

    response = client.delete(f"/api/v1/me/api-keys/{listed[0]['id']}", headers=_headers(key))

    assert response.status_code == 204
    assert client.get("/api/v1/me", headers=_headers(key)).status_code == 401


def test_health_and_docs_stay_open() -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_quote_stream_takes_the_key_from_the_url(alice: tuple[User, str]) -> None:
    """Browsers cannot set WebSocket headers, so the stream accepts ?api_key=."""
    _, key = alice

    with client.websocket_connect(f"/api/v1/ws/quotes?api_key={key}") as socket:
        socket.send_text("not json")
        assert socket.receive_json()["type"] == "error"

    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/api/v1/ws/quotes") as socket:
            socket.receive_json()
    assert refused.value.code == 1008


def test_requests_beyond_the_rate_limit_are_refused(
    monkeypatch: pytest.MonkeyPatch, alice: tuple[User, str], bob: tuple[User, str]
) -> None:
    """Each user has their own budget of requests per minute."""
    monkeypatch.setattr(auth, "get_settings", lambda: Settings(rate_limit_per_minute=2))
    _, alice_key = alice
    _, bob_key = bob

    statuses = [client.get("/api/v1/me", headers=_headers(alice_key)).status_code for _ in range(3)]
    other = client.get("/api/v1/me", headers=_headers(bob_key))

    assert statuses == [200, 200, 429]
    assert other.status_code == 200


# Credentials.


def test_credentials_are_stored_encrypted_and_never_returned(alice: tuple[User, str]) -> None:
    account, key = alice

    response = client.put(
        "/api/v1/me/credentials",
        headers=_headers(key),
        json={"anthropic_api_key": "sk-ant-new-secret-1234", "fmp_api_key": "fmp-secret-5678"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["anthropic"] == {"configured": True, "hint": "…1234"}
    assert body["fmp"] == {"configured": True, "hint": "…5678"}
    assert "secret" not in response.text

    async def stored() -> UserRecord:
        async with get_sessionmaker()() as session:
            record = await session.scalar(select(UserRecord).where(UserRecord.id == account.id))
            assert record is not None
            return record

    record = asyncio.run(stored())
    assert record.anthropic_api_key is not None
    assert "sk-ant-new-secret" not in record.anthropic_api_key


def test_omitted_credentials_are_kept_and_null_removes(alice: tuple[User, str]) -> None:
    _, key = alice
    client.put("/api/v1/me/credentials", headers=_headers(key), json={"fmp_api_key": "fmp-1234567"})

    kept = client.put(
        "/api/v1/me/credentials", headers=_headers(key), json={"anthropic_api_key": None}
    ).json()

    assert kept["fmp"]["configured"] is True
    assert kept["anthropic"] == {"configured": False, "hint": None}


def test_credentials_need_an_encryption_key(
    monkeypatch: pytest.MonkeyPatch, alice: tuple[User, str]
) -> None:
    _, key = alice
    monkeypatch.setattr(
        "app.core.security.get_settings", lambda: Settings(credentials_encryption_key=None)
    )

    response = client.put(
        "/api/v1/me/credentials", headers=_headers(key), json={"fmp_api_key": "fmp-1234567"}
    )

    assert response.status_code == 503
    assert "CREDENTIALS_ENCRYPTION_KEY" in response.json()["detail"]


def test_market_data_is_fetched_with_the_requesters_fmp_key(
    monkeypatch: pytest.MonkeyPatch, alice: tuple[User, str], bob: tuple[User, str]
) -> None:
    """Each request's provider key is its own user's: Bob has none, so he gets none."""
    _, alice_key = alice
    _, bob_key = bob
    client.put(
        "/api/v1/me/credentials", headers=_headers(alice_key), json={"fmp_api_key": "fmp-alice-1"}
    )
    seen: list[str | None] = []

    async def fake_info(symbol: str) -> TickerInfo:
        key = current_provider_keys().fmp
        seen.append(None if key is None else key.get_secret_value())
        return TickerInfo(symbol=symbol, source="fmp")

    monkeypatch.setattr(stocks, "fetch_ticker_info", fake_info)

    client.get("/api/v1/stocks/AAPL", headers=_headers(alice_key))
    client.get("/api/v1/stocks/AAPL", headers=_headers(bob_key))

    assert seen == ["fmp-alice-1", None]


# Isolation of stored results.


def _flat_history(monkeypatch: pytest.MonkeyPatch) -> None:
    index = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=300)
    frame = pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": [100.0 + i for i in range(300)],
         "Volume": 1},
        index=index,
    )  # fmt: skip

    async def fake_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        return frame

    monkeypatch.setattr(backtest, "fetch_price_history", fake_history)


def test_users_see_only_their_own_backtests(
    monkeypatch: pytest.MonkeyPatch, alice: tuple[User, str], bob: tuple[User, str]
) -> None:
    _flat_history(monkeypatch)
    _, alice_key = alice
    _, bob_key = bob
    body = {"strategy": {"type": "buy_and_hold"}, "period": "2y", "attribution": None}
    saved = client.post("/api/v1/stocks/SPY/backtest", headers=_headers(alice_key), json=body)
    backtest_id = saved.json()["id"]

    assert client.get("/api/v1/backtests", headers=_headers(alice_key)).json()["total"] == 1
    assert client.get("/api/v1/backtests", headers=_headers(bob_key)).json()["total"] == 0
    assert (
        client.get(f"/api/v1/backtests/{backtest_id}", headers=_headers(bob_key)).status_code == 404
    )
    assert (
        client.delete(f"/api/v1/backtests/{backtest_id}", headers=_headers(bob_key)).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/backtests/{backtest_id}", headers=_headers(alice_key)).status_code
        == 200
    )


# AI analyses: who pays, and the limits users set.

THESIS = InvestmentThesis(
    headline="Steady.",
    stance="neutral",
    conviction="low",
    summary="Flat.",
    supporting_points=[],
    counterpoints=[],
    what_would_change_the_view=[],
    data_limitations=[],
)


@pytest.fixture
def analysis_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fake every data source and Claude; yield the Anthropic key each report billed."""
    billed: list[str] = []
    index = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=300)
    frame = pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": [100.0 + i % 7 for i in range(300)],
         "Volume": 1},
        index=index,
    )  # fmt: skip

    async def fake_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        return frame

    async def fake_info(symbol: str) -> TickerInfo:
        return TickerInfo(symbol=symbol, source="fmp")

    async def no_factors(*args: Any) -> Any:
        raise DataFetchError("offline")

    async def no_financials(*args: Any) -> Any:
        raise FilingFetchError("offline")

    async def fake_write(
        context: AnalysisContext, kind: str, filings: list[Filing], *, client: AsyncAnthropic
    ) -> tuple[InvestmentThesis, str]:
        billed.append(str(client.api_key))
        return THESIS, "claude-opus-5-5"

    monkeypatch.setattr(research, "fetch_price_history", fake_history)
    monkeypatch.setattr(research, "fetch_ticker_info", fake_info)
    monkeypatch.setattr(research, "fetch_factors", no_factors)
    monkeypatch.setattr(research, "fetch_financials", no_financials)
    monkeypatch.setattr(research, "write_analysis", fake_write)
    yield billed


ANALYSIS = {"kind": "thesis", "include_filings": False}


def test_analyses_are_billed_to_the_requesters_key_and_not_shared(
    analysis_calls: list[str], alice: tuple[User, str], bob: tuple[User, str]
) -> None:
    """Bob's identical request is written on his own key; Alice's report is not reused."""
    _, alice_key = alice
    _, bob_key = bob

    first = client.post("/api/v1/stocks/AAPL/analysis", headers=_headers(alice_key), json=ANALYSIS)
    again = client.post("/api/v1/stocks/AAPL/analysis", headers=_headers(alice_key), json=ANALYSIS)
    other = client.post("/api/v1/stocks/AAPL/analysis", headers=_headers(bob_key), json=ANALYSIS)

    assert [r.status_code for r in (first, again, other)] == [200, 200, 200]
    assert again.json()["cached"] is True
    assert other.json()["cached"] is False
    assert analysis_calls[1] == "sk-ant-bob-key"
    assert len(analysis_calls) == 2
    assert client.get("/api/v1/analyses", headers=_headers(bob_key)).json()["total"] == 1


def test_analyses_need_the_users_own_anthropic_key(
    analysis_calls: list[str], alice: tuple[User, str]
) -> None:
    """Without a stored key nothing is fetched or billed, and no server key is used."""
    _, key = alice
    client.put("/api/v1/me/credentials", headers=_headers(key), json={"anthropic_api_key": None})

    response = client.post("/api/v1/stocks/AAPL/analysis", headers=_headers(key), json=ANALYSIS)

    assert response.status_code == 403
    assert "PUT /api/v1/me/credentials" in response.json()["detail"]
    assert analysis_calls == []


def test_analyses_stop_at_the_users_own_limits(
    analysis_calls: list[str], alice: tuple[User, str]
) -> None:
    """Reused reports are free and do not count; new ones stop at the cap."""
    _, key = alice
    limits = client.put(
        "/api/v1/me/limits",
        headers=_headers(key),
        json={"ai_requests_per_hour": 1, "ai_requests_per_day": None},
    )
    assert limits.json()["limits"] == {"ai_requests_per_hour": 1, "ai_requests_per_day": None}

    first = client.post("/api/v1/stocks/AAPL/analysis", headers=_headers(key), json=ANALYSIS)
    reused = client.post("/api/v1/stocks/AAPL/analysis", headers=_headers(key), json=ANALYSIS)
    over = client.post("/api/v1/stocks/MSFT/analysis", headers=_headers(key), json=ANALYSIS)

    assert (first.status_code, reused.status_code) == (200, 200)
    assert over.status_code == 429
    assert over.headers["Retry-After"] == "3600"
    assert len(analysis_calls) == 1
    usage = client.get("/api/v1/me", headers=_headers(key)).json()["usage"]
    assert usage == {"last_hour": 1, "last_day": 1}


# API keys and the command line.


def test_api_keys_are_shown_once_and_listed_without_the_key(alice: tuple[User, str]) -> None:
    _, key = alice

    created = client.post("/api/v1/me/api-keys", headers=_headers(key), json={"name": "phone"})
    listed = client.get("/api/v1/me/api-keys", headers=_headers(key)).json()

    assert created.status_code == 201
    new_key = created.json()["key"]
    assert new_key.startswith("pq_")
    assert [item["name"] for item in listed] == ["test", "phone"]
    assert all("key" not in item for item in listed)
    assert client.get("/api/v1/me", headers=_headers(new_key)).status_code == 200


def test_other_users_keys_cannot_be_revoked(alice: tuple[User, str], bob: tuple[User, str]) -> None:
    _, alice_key = alice
    bob_account, bob_key = bob
    bob_keys = client.get("/api/v1/me/api-keys", headers=_headers(bob_key)).json()

    response = client.delete(
        f"/api/v1/me/api-keys/{bob_keys[0]['id']}", headers=_headers(alice_key)
    )

    assert response.status_code == 404
    assert client.get("/api/v1/me", headers=_headers(bob_key)).status_code == 200


def test_command_line_creates_accounts_with_imported_keys(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``create --import-env-keys`` stores the server's keys in that one account only."""
    settings = Settings(
        anthropic_api_key=SecretStr("sk-ant-owner-0001"),
        financial_data_api_key=SecretStr("fmp-owner-0002"),
    )
    monkeypatch.setattr(users_cli, "get_settings", lambda: settings)

    async def keep_open() -> None:
        """Leave the test's in-memory database open."""

    monkeypatch.setattr(users_cli, "close_database", keep_open)
    monkeypatch.setattr(
        "sys.argv", ["users", "create", "Owner@Example.com", "--import-env-keys", "--claim-unowned"]
    )

    users_cli.main()

    output = capsys.readouterr().out
    key = output.split("API key (shown once): ")[1].strip()
    me = client.get("/api/v1/me", headers=_headers(key)).json()
    assert me["email"] == "owner@example.com"
    assert me["anthropic"] == {"configured": True, "hint": "…0001"}
    assert me["fmp"] == {"configured": True, "hint": "…0002"}
    assert "Claimed unowned results" in output


def test_command_line_refuses_a_taken_email(monkeypatch: pytest.MonkeyPatch, user: User) -> None:
    monkeypatch.setattr("sys.argv", ["users", "create", user.email])

    with pytest.raises(SystemExit, match="already uses"):
        users_cli.main()
