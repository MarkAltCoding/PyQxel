"""Tests that keys, tokens and sign-in details stay out of logs, errors and responses.

Every address and key here is made up.
"""

import asyncio
import logging

import httpx2
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from app.api.v1.endpoints import signin
from app.core.config import Settings
from app.core.redact import REDACTED, install_log_redaction, redact
from app.data.fetcher import DataFetchError
from app.db.session import create_engine_for
from app.main import app, http_error
from tests.test_signin import _providers, _sign_in

FMP_KEY = "fmpSecretKey1234567890"
PYQXEL_KEY = "pq_" + "k" * 43
ANTHROPIC_KEY = "sk-ant-api03-" + "a" * 40


@pytest.mark.parametrize(
    "secret_text",
    [
        f"https://financialmodelingprep.com/stable/profile?symbol=AAPL&apikey={FMP_KEY}",
        f"https://www.alphavantage.co/query?function=X&apikey={FMP_KEY}",
        f"GET /api/v1/ws/quotes?symbols=AAPL&api_key={FMP_KEY} HTTP/1.1",
        f"/api/v1/auth/github/callback?code={FMP_KEY}&state={FMP_KEY}",
        f"apikey%3D{FMP_KEY}",
        f"Authorization: Bearer {FMP_KEY}",
    ],
)
def test_keys_in_urls_and_headers_are_redacted(secret_text: str) -> None:
    scrubbed = redact(secret_text)

    assert FMP_KEY not in scrubbed
    assert REDACTED in scrubbed


def test_pyqxel_and_anthropic_keys_are_redacted_anywhere() -> None:
    scrubbed = redact(f"keys {PYQXEL_KEY} and {ANTHROPIC_KEY}.")

    assert PYQXEL_KEY not in scrubbed and ANTHROPIC_KEY not in scrubbed


def test_ordinary_text_is_left_alone() -> None:
    message = "status_code=402 for AAPL; the cache key is history:fmp:AAPL"

    assert redact(message) == message


def _provider_error() -> httpx2.HTTPStatusError:
    """The error httpx raises for a provider's 500: its message quotes the URL."""
    request = httpx2.Request("GET", "https://www.alphavantage.co/query", params={"apikey": FMP_KEY})
    response = httpx2.Response(500, request=request)
    try:
        response.raise_for_status()
    except httpx2.HTTPStatusError as exc:
        assert FMP_KEY in str(exc), "the premise: httpx quotes the key"
        return exc
    raise AssertionError("raise_for_status did not raise")


def test_data_fetch_errors_never_carry_the_users_key() -> None:
    error = DataFetchError(f"Alpha Vantage request failed: {_provider_error()}")

    assert FMP_KEY not in str(error)


def test_logs_never_carry_keys_even_in_tracebacks(caplog: pytest.LogCaptureFixture) -> None:
    install_log_redaction()
    logger = logging.getLogger("app.test_privacy")

    with caplog.at_level(logging.INFO):
        logger.warning("Lookup failed: %s", f"?apikey={FMP_KEY}")
        logger.info("Access: %s %s", "GET", f"/ws?api_key={PYQXEL_KEY}")
        logger.info("Finnhub could not fill AAPL: %s", _provider_error())
        try:
            raise _provider_error()
        except httpx2.HTTPStatusError:
            logger.exception("Provider failed")

    formatted = "\n".join(logging.Formatter().format(record) for record in caplog.records)
    assert "Provider failed" in formatted and "HTTPStatusError" in formatted
    assert FMP_KEY not in formatted and PYQXEL_KEY not in formatted


def test_error_details_sent_to_clients_are_scrubbed() -> None:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    exc = HTTPException(status_code=502, detail=f"FMP failed at ?apikey={FMP_KEY}")

    response = asyncio.run(http_error(request, exc))

    assert FMP_KEY not in bytes(response.body).decode()


def test_database_errors_leave_out_emails_and_hashes() -> None:
    async def scenario() -> str:
        engine = create_engine_for("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE people (email TEXT UNIQUE)"))
            insert = text("INSERT INTO people (email) VALUES (:email)")
            await connection.execute(insert, {"email": "private.person@example.com"})
            try:
                await connection.execute(insert, {"email": "private.person@example.com"})
            except IntegrityError as exc:
                return str(exc)
        raise AssertionError("the duplicate was accepted")

    assert "private.person@example.com" not in asyncio.run(scenario())


# Signing in.


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    settings = Settings(
        github_client_id="gh-client",
        github_client_secret=SecretStr("gh-secret"),
        google_client_id="g-client",
        google_client_secret=SecretStr("g-secret"),
    )
    monkeypatch.setattr(signin, "get_settings", lambda: settings)
    return TestClient(app, follow_redirects=False)


def test_sign_in_asks_only_for_email(browser: TestClient) -> None:
    scopes = {}
    for provider in ("github", "google"):
        location = browser.get(f"/api/v1/auth/{provider}/start").headers["location"]
        scopes[provider] = httpx2.URL(location).params["scope"]

    assert scopes == {"github": "user:email", "google": "openid email"}


def test_errors_written_into_the_callback_url_are_not_shown(browser: TestClient) -> None:
    """Anyone can send a link with ``?error=…``; its text never reaches the page."""
    lure = "Your account is locked. Call 555-0100 to restore it"

    response = browser.get("/api/v1/auth/github/callback", params={"error": lure})

    assert lure.split(".")[0].replace(" ", "+") not in response.headers["location"]
    assert "did+not+complete" in response.headers["location"]


def test_sign_in_attempts_are_throttled_per_address(browser: TestClient) -> None:
    answers = [
        browser.get("/api/v1/auth/github/start").status_code
        for _ in range(signin.SIGN_INS_PER_ADDRESS + 1)
    ]
    callback = browser.get("/api/v1/auth/github/callback", params={"code": "c", "state": "s"})

    assert answers[:-1] == [303] * signin.SIGN_INS_PER_ADDRESS
    assert answers[-1] == 429
    assert "Too+many+sign-in+attempts" in callback.headers["location"]


@pytest.mark.parametrize("provider", ["github", "google"])
def test_signing_in_logs_no_email_or_key(
    browser: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    provider: str,
) -> None:
    _providers(monkeypatch)

    with caplog.at_level(logging.DEBUG):
        fragment = _sign_in(browser, provider)

    logged = "\n".join(record.getMessage() for record in caplog.records).lower()
    assert "octo@example.com" not in logged
    assert fragment["key"].lower() not in logged
