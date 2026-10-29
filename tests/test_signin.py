"""Tests for signing in with GitHub or Google, and signing out.

The providers' HTTP APIs are replaced by a mock transport; everything else, the
database included, is the app's own.
"""

import asyncio
import base64
import hashlib
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from app.api.v1.endpoints import signin
from app.core.config import Settings, get_settings
from app.db.session import get_sessionmaker
from app.db.tables import ApiKeyRecord, IdentityRecord, UserRecord
from app.db.users import authenticate, create_api_key, revoke_signed_in_key, user_for_identity
from app.main import app

GITHUB_USER = {"id": 4242, "login": "octo"}
GITHUB_EMAILS = [
    {"email": "octo@users.noreply.github.com", "primary": False, "verified": True},
    {"email": "Octo@Example.com", "primary": True, "verified": True},
]
GOOGLE_USER = {"sub": "google-sub-7", "email": "octo@example.com", "email_verified": True}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A browser that keeps cookies and does not follow redirects, on a server with
    GitHub and Google sign-in set up."""
    settings = Settings(
        github_client_id="gh-client",
        github_client_secret=SecretStr("gh-secret"),
        google_client_id="g-client",
        google_client_secret=SecretStr("g-secret"),
    )
    monkeypatch.setattr(signin, "get_settings", lambda: settings)
    return TestClient(app, follow_redirects=False)


Answer = Callable[[httpx2.Request], httpx2.Response]


def _providers(
    monkeypatch: pytest.MonkeyPatch, answer: Answer | None = None
) -> list[httpx2.Request]:
    """Answer the providers' token and user endpoints; return the requests they got."""
    requests: list[httpx2.Request] = []

    def default(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url.endswith("/login/oauth/access_token") or url.endswith("oauth2.googleapis.com/token"):
            return httpx2.Response(200, json={"access_token": "provider-token"})
        if url == "https://api.github.com/user":
            return httpx2.Response(200, json=GITHUB_USER)
        if url == "https://api.github.com/user/emails":
            return httpx2.Response(200, json=GITHUB_EMAILS)
        if url.startswith("https://openidconnect.googleapis.com/"):
            return httpx2.Response(200, json=GOOGLE_USER)
        return httpx2.Response(404)

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return (answer or default)(request)

    transport = httpx2.MockTransport(handler)
    real_client = httpx2.AsyncClient
    monkeypatch.setattr(
        "app.core.oauth.httpx2.AsyncClient", lambda **kwargs: real_client(transport=transport)
    )
    return requests


def _start(client: TestClient, provider: str, next_page: str = "/") -> dict[str, list[str]]:
    """Start signing in; return the query of the provider URL the browser is sent to."""
    response = client.get(f"/api/v1/auth/{provider}/start", params={"next": next_page})
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers["location"]).query)


def _finish(client: TestClient, provider: str, **params: str) -> dict[str, str]:
    """Come back from the provider; return the fragment the sign-in page is given."""
    response = client.get(f"/api/v1/auth/{provider}/callback", params=params)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/signin#")
    return {name: values[0] for name, values in parse_qs(location.split("#", 1)[1]).items()}


def _sign_in(client: TestClient, provider: str, next_page: str = "/") -> dict[str, str]:
    query = _start(client, provider, next_page)
    return _finish(client, provider, code="the-code", state=query["state"][0])


async def _count(model: Any) -> int:
    async with get_sessionmaker()() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _email_for(key: str) -> str | None:
    async with get_sessionmaker()() as session:
        user = await authenticate(session, key)
        return None if user is None else user.email


def test_providers_lists_only_those_set_up(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.get("/api/v1/auth/providers").json() == [
        {"name": "github", "label": "GitHub"},
        {"name": "google", "label": "Google"},
    ]
    monkeypatch.setattr(signin, "get_settings", lambda: Settings())

    assert client.get("/api/v1/auth/providers").json() == []
    assert client.get("/api/v1/auth/github/start").status_code == 404


def test_starting_sends_the_browser_to_the_provider_with_state_and_pkce(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/auth/github/start", params={"next": "/backtest"})

    location = urlsplit(response.headers["location"])
    query = {name: values[0] for name, values in parse_qs(location.query).items()}
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        "https://github.com/login/oauth/authorize"
    )
    assert query["client_id"] == "gh-client"
    assert query["redirect_uri"] == "http://testserver/api/v1/auth/github/callback"
    assert query["scope"] == "user:email"
    assert query["code_challenge_method"] == "S256"
    state, verifier, _ = client.cookies["pyqxel_sign_in"].split(".")
    assert query["state"] == state
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert query["code_challenge"] == challenge.rstrip(b"=").decode()
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/api/v1/auth" in cookie


def test_github_sign_in_creates_an_account_and_issues_a_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The callback exchanges the code with the PKCE verifier, and the sign-in page gets
    a working API key and the page to go back to."""
    requests = _providers(monkeypatch)

    fragment = _sign_in(client, "github", next_page="/backtest?symbol=SPY")

    assert fragment["next"] == "/backtest?symbol=SPY"
    assert fragment["key"].startswith("pq_")
    assert asyncio.run(_email_for(fragment["key"])) == "octo@example.com"
    token_request = parse_qs(requests[0].content.decode())
    assert token_request["code"] == ["the-code"]
    assert token_request["client_secret"] == ["gh-secret"]
    assert len(token_request["code_verifier"][0]) >= 43
    assert "pyqxel_sign_in" not in client.cookies


def test_github_and_google_with_the_same_email_share_an_account(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _providers(monkeypatch)
    users = asyncio.run(_count(UserRecord))

    github = _sign_in(client, "github")
    google = _sign_in(client, "google")

    assert asyncio.run(_email_for(github["key"])) == asyncio.run(_email_for(google["key"]))
    assert asyncio.run(_count(UserRecord)) == users + 1
    assert asyncio.run(_count(IdentityRecord)) == 2


def test_an_existing_account_is_joined_by_its_verified_email(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An account made before sign-in existed, like the test account, gets the identity."""
    google = {**GOOGLE_USER, "email": "tester@example.com"}
    _providers(
        monkeypatch,
        lambda request: httpx2.Response(
            200,
            json={"access_token": "t"} if str(request.url).endswith("/token") else google,
        ),
    )
    users = asyncio.run(_count(UserRecord))

    fragment = _sign_in(client, "google")

    assert asyncio.run(_email_for(fragment["key"])) == "tester@example.com"
    assert asyncio.run(_count(UserRecord)) == users


def test_a_returning_identity_keeps_its_account_and_updates_its_email() -> None:
    async def scenario() -> tuple[str, str, str]:
        async with get_sessionmaker()() as session:
            first = await user_for_identity(session, "github", "99", "old@example.com")
            again = await user_for_identity(session, "github", "99", "new@example.com")
            identity = await session.scalar(
                select(IdentityRecord).where(IdentityRecord.subject == "99")
            )
            assert identity is not None
            return first.id, again.id, identity.email

    first, again, email = asyncio.run(scenario())

    assert first == again
    assert email == "new@example.com"


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"code": "c", "state": "forged"}, "expired or was started in another browser"),
        ({"code": "c"}, "expired or was started in another browser"),
        ({"error": "access_denied"}, "Signing in with GitHub was cancelled."),
    ],
)
def test_callbacks_that_cannot_be_trusted_are_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, params: dict[str, str], message: str
) -> None:
    requests = _providers(monkeypatch)
    _start(client, "github")

    fragment = _finish(client, "github", **params)

    assert message in fragment["error"]
    assert "key" not in fragment
    assert requests == []


def test_a_callback_without_the_cookie_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state must match the cookie of the browser that started signing in."""
    _providers(monkeypatch)
    state = _start(client, "github")["state"][0]
    client.cookies.clear()

    fragment = _finish(client, "github", code="c", state=state)

    assert "key" not in fragment and "another browser" in fragment["error"]


@pytest.mark.parametrize(
    ("provider", "body", "message"),
    [
        (
            "github",
            [{"email": "x@example.com", "primary": True, "verified": False}],
            "no verified email",
        ),
        ("google", {**GOOGLE_USER, "email_verified": False}, "not verified"),
    ],
)
def test_unverified_emails_are_refused(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    body: object,
    message: str,
) -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url.endswith("token"):
            return httpx2.Response(200, json={"access_token": "t"})
        if url == "https://api.github.com/user":
            return httpx2.Response(200, json=GITHUB_USER)
        return httpx2.Response(200, json=body)

    _providers(monkeypatch, answer)
    users = asyncio.run(_count(UserRecord))

    fragment = _sign_in(client, provider)

    assert message in fragment["error"] and "key" not in fragment
    assert asyncio.run(_count(UserRecord)) == users


def test_a_refused_code_is_explained(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _providers(
        monkeypatch,
        lambda request: httpx2.Response(
            200, json={"error": "bad_verification_code", "error_description": "The code is wrong"}
        ),
    )

    fragment = _sign_in(client, "github")

    assert fragment["error"] == "GitHub did not accept the sign-in: The code is wrong."


@pytest.mark.parametrize(
    "next_page", ["//evil.example/path", "https://evil.example/", "/\\evil.example", "backtest"]
)
def test_signing_in_never_sends_people_to_another_site(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, next_page: str
) -> None:
    _providers(monkeypatch)

    assert _sign_in(client, "github", next_page=next_page)["next"] == "/"


def test_signing_out_revokes_sign_in_keys_but_not_keys_for_scripts(
    client: TestClient, user: Any
) -> None:
    async def keys() -> tuple[str, str]:
        async with get_sessionmaker()() as session:
            signed_in = await create_api_key(session, user.id, "GitHub sign-in", "github")
            script = await create_api_key(session, user.id, "my script")
            return signed_in.key, script.key

    signed_in, script = asyncio.run(keys())

    for key in (signed_in, script):
        response = client.post("/api/v1/auth/signout", headers={"Authorization": f"Bearer {key}"})
        assert response.status_code == 204

    assert asyncio.run(_email_for(signed_in)) is None
    assert asyncio.run(_email_for(script)) == "tester@example.com"
    assert client.post("/api/v1/auth/signout").status_code == 204


async def _expire(key_name: str) -> None:
    """Move the expiry of the keys named ``key_name`` into the past."""
    async with get_sessionmaker()() as session:
        await session.execute(
            update(ApiKeyRecord)
            .where(ApiKeyRecord.name == key_name)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await session.commit()


@pytest.mark.usefixtures("real_auth")
def test_sign_in_keys_expire(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A sign-in key works for SIGN_IN_KEY_DAYS, then is refused like a revoked one."""
    _providers(monkeypatch)
    before = datetime.now(timezone.utc)

    key = _sign_in(client, "github")["key"]

    async def expiry() -> datetime | None:
        async with get_sessionmaker()() as session:
            record: ApiKeyRecord | None = await session.scalar(select(ApiKeyRecord))
            assert record is not None
            return record.expires_at

    expires_at = asyncio.run(expiry())
    assert expires_at is not None
    lifetime = expires_at.replace(tzinfo=timezone.utc) - before
    assert timedelta(days=30) <= lifetime < timedelta(days=30, minutes=1)
    assert asyncio.run(_email_for(key)) == "octo@example.com"

    asyncio.run(_expire("GitHub sign-in"))

    assert asyncio.run(_email_for(key)) is None
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {key}"}).status_code == 401


def test_keys_for_scripts_do_not_expire(user: Any) -> None:
    async def issue() -> str:
        async with get_sessionmaker()() as session:
            issued = await create_api_key(session, user.id, "my script")
            assert issued.expires_at is None
            return issued.key

    assert asyncio.run(_email_for(asyncio.run(issue()))) == "tester@example.com"


def test_signing_in_clears_away_spent_sign_in_keys(user: Any) -> None:
    """Revoked and expired sign-in keys are deleted at the next sign-in; working sign-in
    keys and every key made for scripts are kept."""
    month = timedelta(days=30)

    async def issue(name: str, signed_in_with: str | None = None) -> str:
        async with get_sessionmaker()() as session:
            expires = month if signed_in_with else None
            return (await create_api_key(session, user.id, name, signed_in_with, expires)).key

    async def revoke(key: str) -> None:
        async with get_sessionmaker()() as session:
            assert await revoke_signed_in_key(session, key)

    async def names() -> set[str]:
        async with get_sessionmaker()() as session:
            return set(await session.scalars(select(ApiKeyRecord.name)))

    asyncio.run(revoke(asyncio.run(issue("signed out", "github"))))
    asyncio.run(issue("expired", "google"))
    asyncio.run(_expire("expired"))
    asyncio.run(issue("still signed in", "github"))
    asyncio.run(issue("my script"))

    asyncio.run(issue("new sign-in", "github"))

    assert asyncio.run(names()) == {"still signed in", "my script", "new sign-in"}


@pytest.mark.usefixtures("real_auth")
@pytest.mark.parametrize("provider", ["github", "google"])
def test_admin_settings_show_for_the_admin_email_through_either_sign_in(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """GitHub's primary verified email and Google's verified email both reach the admin."""
    monkeypatch.setattr(get_settings(), "admin_email", "octo@example.com")
    _providers(monkeypatch)

    key = _sign_in(client, provider)["key"]
    account = client.get("/api/v1/me", headers={"Authorization": f"Bearer {key}"}).json()

    assert account["email"] == "octo@example.com"
    assert account["admin"]["yfinance_fallback"] is False


@pytest.mark.usefixtures("real_auth")
@pytest.mark.parametrize("provider", ["github", "google"])
def test_admin_settings_stay_hidden_from_every_other_email(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """A secondary or unverified address that matches the admin's makes no one admin."""
    monkeypatch.setattr(get_settings(), "admin_email", "octo@users.noreply.github.com")
    _providers(monkeypatch)

    key = _sign_in(client, provider)["key"]
    headers = {"Authorization": f"Bearer {key}"}
    account = client.get("/api/v1/me", headers=headers).json()
    changed = client.put("/api/v1/me/admin", headers=headers, json={"yfinance_fallback": True})

    assert account["email"] == "octo@example.com"
    missing = client.put("/api/v1/me/missing", headers=headers, json={"yfinance_fallback": True})
    assert "admin" not in account
    assert (changed.status_code, changed.text) == (missing.status_code, missing.text)
