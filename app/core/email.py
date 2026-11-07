"""Sending the emails that sign people in: a link to choose a password, which also proves
the address is theirs.

Emails go through Resend's HTTP API, which Cloud Run can reach (it blocks outgoing mail on
port 25), or, in development, to the server's log. Without either, signing in with an
email and password is not offered.
"""

import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Protocol

import httpx2
from pydantic import SecretStr

from app.core.config import Settings

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
HTTP_TIMEOUT_SECONDS = 10.0
SEND_ATTEMPTS = 3
"""Tries at sending one email: the first, and two retries after a network error, a reached
rate limit (429), or a fault on Resend's side (5xx). Other refusals are not retried."""
RETRY_WAIT_SECONDS = 2.0
"""Wait before the first retry, doubled for the next, unless Resend's ``Retry-After`` says."""
DEVELOPMENT_URL = "http://localhost:5173"
"""Where emailed links point in development, without ``PUBLIC_URL``: the Vite server."""


class EmailError(Exception):
    """Raised when an email could not be sent."""


@dataclass(frozen=True)
class Email:
    """One message to one address, as plain text and as HTML."""

    to: str
    subject: str
    text: str
    html: str
    idempotency_key: str | None = None
    """Names this one email, so a retry of its send cannot deliver it twice. Resend keeps
    keys for a day."""


class EmailSender(Protocol):
    """Something that delivers an :class:`Email`."""

    async def send(self, email: Email) -> None:
        """Deliver ``email``.

        Raises:
            EmailError: If it could not be sent.
        """
        ...


@dataclass(frozen=True)
class ResendSender:
    """Sends through Resend, from an address on a domain verified there."""

    api_key: SecretStr
    sender: str

    async def send(self, email: Email) -> None:
        """Post ``email`` to Resend, retrying network errors, rate limits, and Resend's own
        faults a couple of times; raise :class:`EmailError` if it is not accepted."""
        headers = {"Authorization": f"Bearer {self.api_key.get_secret_value()}"}
        if email.idempotency_key:
            headers["Idempotency-Key"] = email.idempotency_key
        body = {
            "from": self.sender,
            "to": [email.to],
            "subject": email.subject,
            "text": email.text,
            "html": email.html,
        }
        wait = RETRY_WAIT_SECONDS
        for attempt in range(1, SEND_ATTEMPTS + 1):
            last = attempt == SEND_ATTEMPTS
            try:
                async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
                    response = await client.post(RESEND_URL, headers=headers, json=body)
            except httpx2.HTTPError as exc:
                if last:
                    raise EmailError(f"Resend could not be reached: {exc}") from exc
                await asyncio.sleep(wait)
                wait *= 2
                continue
            if response.status_code < 400:
                return
            if (response.status_code == 429 or response.status_code >= 500) and not last:
                await asyncio.sleep(_retry_after(response) or wait)
                wait *= 2
                continue
            # The body names the problem (an unverified domain, a reached limit), never
            # the message, so it is safe to log.
            raise EmailError(f"Resend refused the email ({response.status_code}): {response.text}")


def _retry_after(response: httpx2.Response) -> float | None:
    """The seconds Resend's ``Retry-After`` header asks for, if it gives a number."""
    try:
        return max(0.0, float(response.headers.get("retry-after", "")))
    except ValueError:
        return None


@dataclass(frozen=True)
class ConsoleSender:
    """Writes each email to the server's error output instead of sending it; for
    development only.

    It writes there rather than to the log, whose redaction would hide the link's token,
    since the point is to follow the link.
    """

    async def send(self, email: Email) -> None:
        """Write ``email`` out, link and all."""
        print(f"Email to {email.to}: {email.subject}\n{email.text}", file=sys.stderr, flush=True)


def email_sender(settings: Settings) -> EmailSender | None:
    """How this server sends sign-in emails, or ``None`` when it cannot."""
    if settings.email_to_console:
        return ConsoleSender()
    if settings.resend_api_key and settings.email_from and settings.public_url:
        return ResendSender(settings.resend_api_key, settings.email_from)
    return None


def site_url(settings: Settings) -> str | None:
    """The website's address emailed links point to, without a trailing slash, or
    ``None`` when no email can be sent."""
    if settings.public_url:
        return settings.public_url.rstrip("/")
    return DEVELOPMENT_URL if settings.email_to_console else None
