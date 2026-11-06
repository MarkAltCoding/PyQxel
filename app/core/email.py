"""Sending the emails that sign people in: a link to choose a password, which also proves
the address is theirs.

Emails go through Resend's HTTP API, which Cloud Run can reach (it blocks outgoing mail on
port 25), or, in development, to the server's log. Without either, signing in with an
email and password is not offered.
"""

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
        """Post ``email`` to Resend; raise :class:`EmailError` if it is refused."""
        try:
            async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    RESEND_URL,
                    headers={"Authorization": f"Bearer {self.api_key.get_secret_value()}"},
                    json={
                        "from": self.sender,
                        "to": [email.to],
                        "subject": email.subject,
                        "text": email.text,
                        "html": email.html,
                    },
                )
        except httpx2.HTTPError as exc:
            raise EmailError(f"Resend could not be reached: {exc}") from exc
        if response.status_code >= 400:
            # The body names the problem (an unverified domain, a reached limit), never
            # the message, so it is safe to log.
            raise EmailError(f"Resend refused the email ({response.status_code}): {response.text}")


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
