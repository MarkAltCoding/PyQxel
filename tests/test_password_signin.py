"""Tests for signing in with an email and password: making an account and choosing a
password by an emailed link, signing in, rate limits, and attempts to reach someone
else's account.

Emails are caught by a fake sender instead of going out; everything else, the database
included, is the app's own.
"""

import asyncio
import smtplib
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, update

from app.api.v1.endpoints import password_signin
from app.core.config import Settings
from app.core.email import Email, email_sender, site_url
from app.core.passwords import password_problem, verify_password
from app.db.passwords import EmailLink
from app.db.session import get_sessionmaker
from app.db.tables import ApiKeyRecord, EmailLinkRecord, UserRecord
from app.db.users import authenticate, create_api_key, delete_user, user_for_identity
from app.main import app

GOOD_PASSWORD = "correct horse battery staple"
SITE = "https://pyqxel.example"


class FakeSender:
    """Keeps what would have been emailed."""

    def __init__(self) -> None:
        self.sent: list[Email] = []

    async def send(self, email: Email) -> None:
        self.sent.append(email)

    def token_for(self, address: str) -> str:
        """The token in the last link emailed to ``address``."""
        email = next(email for email in reversed(self.sent) if email.to == address)
        link = next(word for word in email.text.split() if word.startswith(SITE))
        assert urlsplit(link).query == "", "the token must not be in the query"
        return link.split("#token=", 1)[1]


@pytest.fixture
def mail(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeSender]:
    """A server that sends sign-in emails, caught by a fake sender."""
    sender = FakeSender()
    settings = Settings(
        public_url=SITE, resend_api_key=SecretStr("re_test"), email_from="PyQxel <a@b.example>"
    )
    monkeypatch.setattr(password_signin, "get_settings", lambda: settings)
    monkeypatch.setattr(password_signin, "email_sender", lambda settings: sender)
    yield sender


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _address(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    """Make every request seem to come from the client address ``address``."""
    monkeypatch.setattr(password_signin, "_address", lambda request: address)


def _ask(client: TestClient, email: str, **headers: str) -> Any:
    return client.post("/api/v1/auth/password/link", json={"email": email}, headers=headers)


def _set(client: TestClient, token: str, password: str = GOOD_PASSWORD) -> Any:
    return client.post("/api/v1/auth/password/set", json={"token": token, "password": password})


def _sign_in(client: TestClient, email: str, password: str = GOOD_PASSWORD) -> Any:
    return client.post("/api/v1/auth/password/sign-in", json={"email": email, "password": password})


def _make_account(client: TestClient, mail: FakeSender, email: str) -> str:
    """Make an account at ``email`` with the good password; return its first key."""
    assert _ask(client, email).status_code == 202
    response = _set(client, mail.token_for(email))
    assert response.status_code == 200, response.text
    return str(response.json()["key"])


async def _count(model: Any) -> int:
    async with get_sessionmaker()() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _email_for(key: str) -> str | None:
    async with get_sessionmaker()() as session:
        user = await authenticate(session, key)
        return None if user is None else user.email


async def _password_hash(email: str) -> str | None:
    async with get_sessionmaker()() as session:
        return await session.scalar(
            select(UserRecord.password_hash).where(UserRecord.email == email)
        )


def test_it_is_offered_only_when_email_can_be_sent(client: TestClient) -> None:
    """Without a sender, the website does not offer it and its routes answer 404."""
    assert email_sender(Settings()) is None and site_url(Settings()) is None
    assert email_sender(Settings(resend_api_key=SecretStr("re_x"), email_from="a@b.c")) is None
    assert client.get("/api/v1/auth/password").json() == {"available": False, "min_length": 12}
    assert _ask(client, "someone@example.com").status_code == 404
    assert _sign_in(client, "someone@example.com").status_code == 404
    console = Settings(email_to_console=True)
    assert email_sender(console) is not None and site_url(console) == "http://localhost:5173"


def test_making_an_account_by_emailed_link(client: TestClient, mail: FakeSender) -> None:
    """Asking stores only a link; the account is made when the link is used, and its
    password then signs in."""
    assert client.get("/api/v1/auth/password").json()["available"] is True
    users = asyncio.run(_count(UserRecord))

    asked = _ask(client, "  New.Person@Example.COM ")

    assert asked.status_code == 202
    assert "new.person@example.com" in asked.json()["message"]
    assert "token" not in asked.text
    assert asyncio.run(_count(UserRecord)) == users, "no account before the link is used"
    assert mail.sent[0].to == "new.person@example.com"
    assert mail.sent[0].subject == "Finish making your PyQxel account"
    token = mail.token_for("new.person@example.com")
    checked = client.post("/api/v1/auth/password/check", json={"token": token})
    assert checked.json() == {"email": "new.person@example.com"}

    made = _set(client, token)

    assert made.status_code == 200
    assert asyncio.run(_email_for(made.json()["key"])) == "new.person@example.com"
    stored = asyncio.run(_password_hash("new.person@example.com"))
    assert stored is not None and stored.startswith("$argon2id$")
    assert GOOD_PASSWORD not in stored
    signed_in = _sign_in(client, "NEW.person@example.com")
    assert signed_in.status_code == 200
    assert asyncio.run(_email_for(signed_in.json()["key"])) == "new.person@example.com"


def test_a_link_works_once_and_not_after_it_expires(client: TestClient, mail: FakeSender) -> None:
    _ask(client, "once@example.com")
    token = mail.token_for("once@example.com")
    assert _set(client, token).status_code == 200
    assert _set(client, token, "a different long password").status_code == 400

    _ask(client, "late@example.com")
    late = mail.token_for("late@example.com")

    async def expire() -> None:
        async with get_sessionmaker()() as session:
            await session.execute(
                update(EmailLinkRecord).values(
                    expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
                )
            )
            await session.commit()

    asyncio.run(expire())
    assert _set(client, late).status_code == 400
    assert client.post("/api/v1/auth/password/check", json={"token": late}).status_code == 400


def test_asking_again_replaces_the_earlier_link(client: TestClient, mail: FakeSender) -> None:
    _ask(client, "twice@example.com")
    first = mail.token_for("twice@example.com")
    _ask(client, "twice@example.com")
    assert _set(client, first).status_code == 400
    assert _set(client, mail.token_for("twice@example.com")).status_code == 200


@pytest.mark.parametrize(
    ("password", "problem"),
    [
        ("short", "at least 12"),
        ("x" * 257, "at most 256"),
        ("weak@example.com", "other than your email"),
        ("abababababababab", "one character repeated"),
    ],
)
def test_weak_passwords_are_refused_and_the_link_still_works(
    client: TestClient, mail: FakeSender, password: str, problem: str
) -> None:
    _ask(client, "weak@example.com")
    token = mail.token_for("weak@example.com")

    refused = _set(client, token, password)

    assert refused.status_code == 422
    assert problem in refused.json()["detail"]
    assert _set(client, token).status_code == 200


def test_password_problem_allows_long_passphrases() -> None:
    assert password_problem(GOOD_PASSWORD, "a@b.example") is None
    assert password_problem("Tr0ub4dor&3xy", "a@b.example") is None


def test_wrong_passwords_and_unknown_addresses_get_the_same_answer(
    client: TestClient, mail: FakeSender, user: Any
) -> None:
    """Nothing tells an unknown address from a wrong password, or from an account (the
    test account) that has no password."""
    _make_account(client, mail, "known@example.com")

    wrong = _sign_in(client, "known@example.com", "not the password at all")
    unknown = _sign_in(client, "nobody@example.com")
    no_password = _sign_in(client, "tester@example.com")

    assert wrong.status_code == unknown.status_code == no_password.status_code == 401
    assert wrong.json() == unknown.json() == no_password.json()


def test_asking_for_a_link_says_the_same_with_or_without_an_account(
    client: TestClient, mail: FakeSender, user: Any
) -> None:
    """The page that asked cannot tell; only the email, read by the address's owner, is
    worded for an account."""
    existing = _ask(client, "tester@example.com")
    new = _ask(client, "stranger@example.com")

    assert existing.status_code == new.status_code == 202
    assert existing.json()["message"].replace("tester", "X") == new.json()["message"].replace(
        "stranger", "X"
    )
    assert mail.sent[0].subject == "Add a password to your PyQxel account"
    assert mail.sent[1].subject == "Finish making your PyQxel account"


def test_asking_for_someone_elses_address_gives_the_asker_nothing(
    client: TestClient, mail: FakeSender, user: Any
) -> None:
    """An attacker who asks for a link at the victim's address gets no token, and the
    victim's account is unchanged until the victim uses the link."""
    victim_key = asyncio.run(_victim_key(user.id))

    answer = _ask(client, "tester@example.com")

    assert mail.token_for("tester@example.com") not in answer.text
    assert [email.to for email in mail.sent] == ["tester@example.com"]
    assert asyncio.run(_password_hash("tester@example.com")) is None
    assert asyncio.run(_email_for(victim_key)) == "tester@example.com"
    # A guessed token does nothing.
    assert _set(client, "a" * 43).status_code == 400
    assert asyncio.run(_password_hash("tester@example.com")) is None


async def _victim_key(user_id: str) -> str:
    async with get_sessionmaker()() as session:
        issued = await create_api_key(session, user_id, "GitHub sign-in", signed_in_with="github")
        return issued.key


def test_a_link_to_one_address_cannot_set_another_accounts_password(
    client: TestClient, mail: FakeSender, user: Any
) -> None:
    """The token is bound to the address it was sent to; the address cannot be swapped."""
    _ask(client, "attacker@evil.example")
    token = mail.token_for("attacker@evil.example")

    response = client.post(
        "/api/v1/auth/password/set",
        json={"token": token, "password": GOOD_PASSWORD, "email": "tester@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "attacker@evil.example"
    assert asyncio.run(_password_hash("tester@example.com")) is None
    assert _sign_in(client, "tester@example.com").status_code == 401


def test_an_unverified_sign_up_cannot_be_joined_by_the_real_owner_later(
    client: TestClient, mail: FakeSender
) -> None:
    """Pre-hijacking: an attacker asks for a link at an address with no account, which
    makes no account, so when the address's owner later signs in with Google they get a
    new account of their own, with no password anyone else chose."""
    users = asyncio.run(_count(UserRecord))
    _ask(client, "victim@example.com")
    assert asyncio.run(_count(UserRecord)) == users

    async def owner_signs_in_with_google() -> str:
        async with get_sessionmaker()() as session:
            account = await user_for_identity(session, "google", "g-1", "victim@example.com")
            return account.id

    asyncio.run(owner_signs_in_with_google())

    assert asyncio.run(_password_hash("victim@example.com")) is None
    assert _sign_in(client, "victim@example.com").status_code == 401


def test_a_password_set_by_link_joins_the_account_of_that_address(
    client: TestClient, mail: FakeSender, user: Any
) -> None:
    """The owner of an existing account adds a password by its link, and both ways sign
    in to the one account."""
    users = asyncio.run(_count(UserRecord))
    _ask(client, "tester@example.com")

    made = _set(client, mail.token_for("tester@example.com"))

    assert made.json()["email"] == "tester@example.com"
    assert asyncio.run(_count(UserRecord)) == users
    assert _sign_in(client, "tester@example.com").status_code == 200


def test_emailed_links_point_at_the_configured_site_whatever_the_host_header(
    client: TestClient, mail: FakeSender
) -> None:
    """A request's Host header is the sender's to write; the link must not follow it."""
    _ask(client, "host@example.com", host="evil.example")

    text = mail.sent[0].text
    assert f"{SITE}/signin/password#token=" in text
    assert "evil.example" not in text + mail.sent[0].html


def test_replacing_a_password_signs_out_what_the_old_one_signed_in(
    client: TestClient, mail: FakeSender
) -> None:
    """Forgot password: the new password works, the old one does not, and keys the old
    one issued are revoked, while keys from other sign-ins stay."""
    first = _make_account(client, mail, "reset@example.com")
    other = _sign_in(client, "reset@example.com").json()["key"]

    async def github_key() -> str:
        async with get_sessionmaker()() as session:
            account = await session.scalar(
                select(UserRecord.id).where(UserRecord.email == "reset@example.com")
            )
            assert account is not None
            return (await create_api_key(session, account, "GitHub", signed_in_with="github")).key

    github = asyncio.run(github_key())
    _ask(client, "reset@example.com")
    assert mail.sent[-1].subject == "Reset your PyQxel password"

    reset = _set(client, mail.token_for("reset@example.com"), "a brand new long password")

    assert reset.status_code == 200
    assert asyncio.run(_email_for(first)) is None
    assert asyncio.run(_email_for(other)) is None
    assert asyncio.run(_email_for(github)) == "reset@example.com"
    assert asyncio.run(_email_for(reset.json()["key"])) == "reset@example.com"
    assert _sign_in(client, "reset@example.com").status_code == 401
    assert _sign_in(client, "reset@example.com", "a brand new long password").status_code == 200


def test_password_attempts_on_one_account_are_limited_across_addresses(
    client: TestClient, mail: FakeSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guessing from many addresses is no faster: after ten tries in 15 minutes the
    account refuses even the right password until the window passes."""
    _make_account(client, mail, "guarded@example.com")
    for attempt in range(password_signin.ATTEMPTS_PER_ACCOUNT):
        _address(monkeypatch, f"203.0.113.{attempt}")
        assert _sign_in(client, "guarded@example.com", f"guess number {attempt}").status_code == 401

    _address(monkeypatch, "198.51.100.1")
    blocked = _sign_in(client, "guarded@example.com")

    assert blocked.status_code == 429
    assert "Forgot password" in blocked.json()["detail"]
    assert 0 < int(blocked.headers["Retry-After"]) <= password_signin.ACCOUNT_WINDOW_SECONDS
    assert _sign_in(client, "someone-else@example.com").status_code == 401


def test_password_attempts_from_one_address_are_limited(
    client: TestClient, mail: FakeSender
) -> None:
    for attempt in range(password_signin.ATTEMPTS_PER_ADDRESS):
        assert _sign_in(client, f"person{attempt}@example.com").status_code == 401
    assert _sign_in(client, "one-more@example.com").status_code == 429


def test_links_to_one_address_are_limited_so_no_inbox_is_flooded(
    client: TestClient, mail: FakeSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    for attempt in range(password_signin.LINKS_PER_EMAIL):
        _address(monkeypatch, f"203.0.113.{attempt}")
        assert _ask(client, "inbox@example.com").status_code == 202
    _address(monkeypatch, "198.51.100.9")
    assert _ask(client, "inbox@example.com").status_code == 429
    assert len(mail.sent) == password_signin.LINKS_PER_EMAIL


def test_links_from_one_address_are_limited(client: TestClient, mail: FakeSender) -> None:
    for attempt in range(password_signin.LINKS_PER_ADDRESS):
        assert _ask(client, f"inbox{attempt}@example.com").status_code == 202
    assert _ask(client, "inbox-more@example.com").status_code == 429


def test_something_that_is_not_an_address_is_refused(client: TestClient, mail: FakeSender) -> None:
    assert _ask(client, "not an address").status_code == 422
    assert _ask(client, "two@at@example.com").status_code == 422
    assert mail.sent == []


def test_the_app_signs_in_without_a_browser_and_its_key_is_named_so(
    client: TestClient, mail: FakeSender
) -> None:
    _make_account(client, mail, "app@example.com")

    response = client.post(
        "/api/v1/auth/password/sign-in",
        json={"email": "app@example.com", "password": GOOD_PASSWORD, "app": True},
        headers={"Origin": "tauri://localhost"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "tauri://localhost"

    async def names() -> list[str]:
        async with get_sessionmaker()() as session:
            return list(await session.scalars(select(ApiKeyRecord.name)))

    assert "Email sign-in (app)" in asyncio.run(names())


def test_signing_out_revokes_a_password_sign_in_key(client: TestClient, mail: FakeSender) -> None:
    key = _make_account(client, mail, "out@example.com")
    signed_out = client.post("/api/v1/auth/signout", headers={"Authorization": f"Bearer {key}"})
    assert signed_out.status_code == 204
    assert asyncio.run(_email_for(key)) is None


def test_deleting_an_account_deletes_its_unused_links(client: TestClient, mail: FakeSender) -> None:
    _make_account(client, mail, "gone@example.com")
    _ask(client, "gone@example.com")
    assert asyncio.run(_count(EmailLinkRecord)) == 1

    async def delete() -> None:
        async with get_sessionmaker()() as session:
            account = await session.scalar(
                select(UserRecord.id).where(UserRecord.email == "gone@example.com")
            )
            assert account is not None
            assert await delete_user(session, account)

    asyncio.run(delete())
    assert asyncio.run(_count(EmailLinkRecord)) == 0


def test_verify_password_refuses_without_a_hash() -> None:
    assert verify_password(None, "anything") is False
    assert verify_password("not a hash", "anything") is False


def test_the_link_email_escapes_its_link() -> None:
    link = EmailLink(token="t", has_account=False, has_password=False)
    email = password_signin.link_email("a@b.example", 'https://x.example/"><script>', link)
    assert "<script>" not in email.html


def test_a_refused_password_is_never_sent_back(client: TestClient, mail: FakeSender) -> None:
    """A password too long for the schema is refused without repeating it."""
    secret = "my secret password " * 60
    response = _sign_in(client, "echo@example.com", secret)
    assert response.status_code == 422
    assert "my secret password" not in response.text


def _resend(monkeypatch: pytest.MonkeyPatch, answers: list[Any]) -> list[Any]:
    """Answer Resend's API with ``answers`` in turn (a status, or an exception to raise),
    without waiting between retries; return the requests it got."""
    import httpx2

    requests: list[Any] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        answer = answers[len(requests) - 1]
        if isinstance(answer, Exception):
            raise answer
        return httpx2.Response(answer, json={"id": "email-1"}, headers={"retry-after": "0"})

    transport = httpx2.MockTransport(handler)
    real_client = httpx2.AsyncClient
    monkeypatch.setattr(
        "app.core.email.httpx2.AsyncClient", lambda **kwargs: real_client(transport=transport)
    )

    async def no_wait(seconds: float) -> None:
        return None

    monkeypatch.setattr("app.core.email.asyncio.sleep", no_wait)
    return requests


def _message() -> Email:
    link = EmailLink(token="t", has_account=False, has_password=False)
    return password_signin.link_email("a@b.example", f"{SITE}/signin/password#token=abc", link)


def test_resend_gets_an_idempotency_key_and_never_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.email import ResendSender

    requests = _resend(monkeypatch, [200])
    asyncio.run(ResendSender(SecretStr("re_x"), "PyQxel <a@b.example>").send(_message()))

    key = requests[0].headers["Idempotency-Key"]
    assert key.startswith("link-") and "abc" not in key
    assert requests[0].headers["Authorization"] == "Bearer re_x"


def test_resend_is_retried_on_limits_and_faults_with_the_same_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.email import ResendSender

    requests = _resend(monkeypatch, [429, 503, 200])
    asyncio.run(ResendSender(SecretStr("re_x"), "PyQxel <a@b.example>").send(_message()))

    assert len(requests) == 3
    assert len({request.headers["Idempotency-Key"] for request in requests}) == 1


def test_resend_refusals_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal such as an unverified domain (403) will not change on a retry."""
    from app.core.email import EmailError, ResendSender

    requests = _resend(monkeypatch, [403])
    with pytest.raises(EmailError, match="403"):
        asyncio.run(ResendSender(SecretStr("re_x"), "PyQxel <a@b.example>").send(_message()))
    assert len(requests) == 1


def test_resend_retries_give_up_after_three_tries(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.email import EmailError, ResendSender

    requests = _resend(monkeypatch, [500, 500, 500, 500])
    with pytest.raises(EmailError, match="500"):
        asyncio.run(ResendSender(SecretStr("re_x"), "PyQxel <a@b.example>").send(_message()))
    assert len(requests) == 3


class FakeMailServer:
    """Stands in for smtplib.SMTP: records each connection, and answers each delivery
    with the next of ``outcomes`` (None to accept, or an exception to raise)."""

    outcomes: list[Exception | None] = []
    sessions: list[dict[str, Any]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.session: dict[str, Any] = {"host": host, "port": port, "steps": []}
        FakeMailServer.sessions.append(self.session)

    def __enter__(self) -> "FakeMailServer":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self, context: Any) -> None:
        self.session["steps"].append("starttls")

    def login(self, user: str, password: str) -> None:
        self.session["steps"].append(("login", user, password))
        outcome = FakeMailServer.outcomes[len(FakeMailServer.sessions) - 1]
        if isinstance(outcome, smtplib.SMTPAuthenticationError):
            raise outcome

    def send_message(self, message: Any) -> None:
        outcome = FakeMailServer.outcomes[len(FakeMailServer.sessions) - 1]
        if outcome is not None:
            raise outcome
        self.session["message"] = message


def _gmail(monkeypatch: pytest.MonkeyPatch, outcomes: list[Exception | None]) -> Any:
    from app.core.email import SmtpSender

    FakeMailServer.outcomes = outcomes
    FakeMailServer.sessions = []
    monkeypatch.setattr("app.core.email.smtplib.SMTP", FakeMailServer)

    async def no_wait(seconds: float) -> None:
        return None

    monkeypatch.setattr("app.core.email.asyncio.sleep", no_wait)
    return SmtpSender(
        "smtp.gmail.com",
        587,
        "pyqxel.app@gmail.com",
        SecretStr("app-pass"),
        "PyQxel <pyqxel.app@gmail.com>",
    )


def test_gmail_sends_over_tls_as_the_account_with_text_and_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = _gmail(monkeypatch, [None])

    asyncio.run(sender.send(_message()))

    session = FakeMailServer.sessions[0]
    assert (session["host"], session["port"]) == ("smtp.gmail.com", 587)
    assert session["steps"] == ["starttls", ("login", "pyqxel.app@gmail.com", "app-pass")]
    message = session["message"]
    assert message["From"] == "PyQxel <pyqxel.app@gmail.com>"
    assert message["To"] == "a@b.example"
    assert message.get_content_type() == "multipart/alternative"
    parts = [part.get_content_type() for part in message.iter_parts()]
    assert parts == ["text/plain", "text/html"]
    assert f"{SITE}/signin/password#token=abc" in message.get_body(("plain",)).get_content()


def test_gmail_retries_temporary_refusals_and_dropped_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = _gmail(
        monkeypatch,
        [
            smtplib.SMTPResponseException(421, b"Try again later"),
            smtplib.SMTPServerDisconnected(),
            None,
        ],
    )
    asyncio.run(sender.send(_message()))
    assert len(FakeMailServer.sessions) == 3
    assert "message" in FakeMailServer.sessions[2]


def test_gmail_does_not_retry_a_refused_sign_in_or_a_permanent_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.email import EmailError

    sender = _gmail(monkeypatch, [smtplib.SMTPAuthenticationError(535, b"Bad credentials")])
    with pytest.raises(EmailError, match="app password") as refused:
        asyncio.run(sender.send(_message()))
    assert "app-pass" not in str(refused.value)
    assert len(FakeMailServer.sessions) == 1

    sender = _gmail(monkeypatch, [smtplib.SMTPResponseException(550, b"No such user")])
    with pytest.raises(EmailError, match="550"):
        asyncio.run(sender.send(_message()))
    assert len(FakeMailServer.sessions) == 1


def test_gmail_is_used_when_set_up_and_resend_is_not_needed() -> None:
    from app.core.email import ResendSender, SmtpSender, email_sender

    gmail = Settings(
        smtp_host="smtp.gmail.com",
        smtp_username="pyqxel.app@gmail.com",
        smtp_password=SecretStr("app-pass"),
        email_from="PyQxel <pyqxel.app@gmail.com>",
        public_url=SITE,
    )
    assert isinstance(email_sender(gmail), SmtpSender)
    assert email_sender(gmail.model_copy(update={"public_url": None})) is None
    assert email_sender(gmail.model_copy(update={"smtp_password": None})) is None
    resend = Settings(resend_api_key=SecretStr("re_x"), email_from="a@b.example", public_url=SITE)
    assert isinstance(email_sender(resend), ResendSender)
