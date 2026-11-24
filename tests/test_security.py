"""Protections against spoofed addresses, look-alike emails, open redirects, unbounded
memory, a changed docs page, and missing security headers."""

import asyncio

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api import docs
from app.api.v1.endpoints import signin
from app.api.v1.endpoints.signin import _destination
from app.core import rate_limit
from app.core.identity import client_address, normalize_email
from app.db.session import get_sessionmaker
from app.db.users import create_user, user_for_identity
from app.main import app


def _request(headers: list[tuple[bytes, bytes]], client: str = "10.0.0.1") -> Request:
    return Request(
        {"type": "http", "headers": headers, "client": (client, 1234), "method": "GET", "path": "/"}
    )


def test_the_client_address_is_the_one_cloud_run_appended() -> None:
    spoofed = _request([(b"x-forwarded-for", b"1.2.3.4, 5.6.7.8, 203.0.113.9")])
    assert client_address(spoofed) == "203.0.113.9"
    assert client_address(_request([])) == "10.0.0.1"


def test_a_spoofed_forwarded_header_does_not_get_a_fresh_sign_in_limit() -> None:
    rate_limit.clear_local_counts()

    async def attempts() -> int:
        made = 0
        for n in range(signin.SIGN_INS_PER_ADDRESS + 5):
            spoofed = _request([(b"x-forwarded-for", f"198.51.100.{n}, 203.0.113.9".encode())])
            try:
                await signin._throttle(spoofed)
            except rate_limit.RateLimitExceeded:
                break
            made += 1
        return made

    assert asyncio.run(attempts()) == signin.SIGN_INS_PER_ADDRESS
    rate_limit.clear_local_counts()


def test_emails_are_lowered_except_letters_that_become_ascii() -> None:
    assert normalize_email("  Someone@Example.COM ") == "someone@example.com"
    assert normalize_email("ÄBC@example.com") == "äbc@example.com"
    kelvin = "marK@example.com"
    assert normalize_email(kelvin) != "mark@example.com"


def test_a_look_alike_address_does_not_join_another_account() -> None:
    async def scenario() -> tuple[str, str]:
        async with get_sessionmaker()() as session:
            owner = await create_user(session, "mark@example.com")
            other = await user_for_identity(session, "github", "999", "MARK@example.com")
            return owner.id, other.id

    owner, other = asyncio.run(scenario())
    assert owner != other


def test_sign_in_destinations_stay_on_this_site() -> None:
    assert _destination("/stocks/AAPL?x=1#ai-report") == "/stocks/AAPL?x=1#ai-report"
    for outside in (
        "//evil.example",
        "/\\evil.example",
        "/\t/evil.example",
        "/\n/evil",
        "https://x",
    ):
        assert _destination(outside) == "/"


def test_rate_limit_counts_of_ended_windows_are_dropped() -> None:
    rate_limit.clear_local_counts()

    async def count(name: str, now: float) -> None:
        await rate_limit.check_rate_limit(name, 100, now=now)

    async def scenario() -> None:
        for n in range(500):
            await count(f"sign-in:198.51.100.{n}", 1_000_000.0)
        await count("someone-else", 1_000_000.0 + 3 * rate_limit.WINDOW_SECONDS)

    asyncio.run(scenario())
    assert len(rate_limit._local) == 1
    rate_limit.clear_local_counts()


def test_responses_carry_security_headers() -> None:
    response = TestClient(app, base_url="https://testserver").get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "max-age" in response.headers["strict-transport-security"]
    assert "camera=()" in response.headers["permissions-policy"]
    plain = TestClient(app).get("/health")
    assert "strict-transport-security" not in plain.headers


def test_the_docs_page_pins_swagger_and_checks_its_files() -> None:
    client = TestClient(app)
    page = client.get("/docs")
    assert page.status_code == 200
    assert docs.SWAGGER_JS_INTEGRITY in page.text and docs.SWAGGER_CSS_INTEGRITY in page.text
    assert f"swagger-ui-dist@{docs.SWAGGER_VERSION}/" in page.text
    assert "<script>" not in page.text  # no inline script: it starts from /docs/start.js
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert client.get("/docs/start.js").headers["content-type"].startswith("text/javascript")
    assert client.get("/redoc").status_code == 404
