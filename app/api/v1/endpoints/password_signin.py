"""Signing in with an email address and a password, on the website and in the native app.

Making an account and forgetting a password are the same thing here: the person asks
for a link at their address, and chooses a password on the page it opens. Nothing is
attached to an address before its link is used, so an account, or a password on an
existing account, is only ever made by someone who reads that address's email. The
answer to asking is the same whether or not the address has an account, and so is the
refusal of a wrong password, so neither tells anyone which addresses have accounts.

The native app signs in here directly, with no browser: these routes answer the app's
origin like any other. Only choosing a password happens on the website, where the
emailed link opens.

Attempts are limited per client address, per email address, and for the whole server,
so guessing one account's password from many addresses is as slow as from one.
"""

import html
import logging
import re
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.email import Email, EmailError, EmailSender, email_sender, site_url
from app.core.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, password_problem
from app.core.rate_limit import RateLimitExceeded, check_rate_limit
from app.db.passwords import (
    EMAIL_LINK_MINUTES,
    PASSWORD_SIGN_IN,
    EmailLink,
    email_for_link,
    new_email_link,
    normalize_email,
    use_email_link,
    user_for_password,
)
from app.db.session import get_session
from app.db.users import create_api_key

logger = logging.getLogger(__name__)

router = APIRouter()
"""Routes under ``/auth/password``."""

Session = Annotated[AsyncSession, Depends(get_session)]

EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
"""An address with one ``@`` and a dot after it; whether it receives mail is proven by
the link, not by its form."""

HOUR = 60 * 60.0
ATTEMPTS_PER_ADDRESS: int = 30
"""Password sign-ins one client address may try a minute. Generous, since many people can
share one address (a campus, an office, or a phone carrier's network); the limit per
account is what stops guessing one person's password."""
ATTEMPTS_PER_ACCOUNT: int = 10
"""Password sign-ins one email address may be tried with in ``ACCOUNT_WINDOW_SECONDS``,
from any number of client addresses."""
ACCOUNT_WINDOW_SECONDS: float = 15 * 60.0
ATTEMPTS_PER_SERVER: int = 300
"""Password sign-ins the whole server takes a minute."""
LINKS_PER_ADDRESS: int = 20
"""Links one client address may ask for an hour, enough for a class or an office signing up
together from one shared address."""
LINKS_PER_EMAIL: int = 3
"""Links one email address may be sent an hour, so nobody can flood someone's inbox."""
LINKS_PER_DAY: int = 90
"""Links the whole server sends a day, under the 100 a day of Resend's free plan."""


class PasswordSignInInfo(BaseModel):
    """Whether this server offers signing in with an email and password."""

    available: bool = Field(
        description="Whether it does: it needs a way to send email, set up by the server's "
        "owner."
    )
    min_length: int = Field(description="Fewest characters a password may have.")


class EmailAddress(BaseModel):
    """An address to send a link to, to make an account or choose a new password."""

    email: str = Field(min_length=3, max_length=320, description="The email address.")


class LinkSent(BaseModel):
    """What asking for a link says, the same whether or not the address has an account."""

    message: str = Field(description="What to do next.")


class LinkToken(BaseModel):
    """The token from an emailed link."""

    token: str = Field(min_length=20, max_length=128, description="The link's token.")


class LinkAddress(BaseModel):
    """The address a working link was sent to."""

    email: str = Field(description="The address the password will belong to.")


class NewPassword(BaseModel):
    """The password chosen on the page an emailed link opens."""

    token: str = Field(min_length=20, max_length=128, description="The link's token.")
    # Its length is checked by ``password_problem``, which explains a refusal plainly.
    password: str = Field(
        max_length=4 * MAX_PASSWORD_LENGTH,
        description=f"The new password, {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} "
        "characters.",
    )


class PasswordSignIn(BaseModel):
    """An email and password to sign in with."""

    email: str = Field(min_length=3, max_length=320, description="The account's email address.")
    password: str = Field(
        min_length=1, max_length=MAX_PASSWORD_LENGTH, description="The account's password."
    )
    app: bool = Field(
        default=False, description="Whether the native app is signing in, which names its key."
    )


class PasswordSignedIn(BaseModel):
    """The API key a password sign-in issued, and whose account it is."""

    key: str = Field(description="The new API key, shown this once.")
    email: str = Field(description="The account's email address.")


def _too_many(exc: RateLimitExceeded, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=message,
        headers={"Retry-After": str(exc.retry_after)},
    )


def _address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _sender() -> tuple[EmailSender, str]:
    """How emails are sent and the site their links point to, or a 404 without them."""
    settings = get_settings()
    sender, site = email_sender(settings), site_url(settings)
    if sender is None or site is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signing in with an email and password is not set up on this server.",
        )
    return sender, site


def _email(value: str) -> str:
    """``value`` as accounts store addresses, or a 422 if it is not one."""
    email = normalize_email(value)
    if not EMAIL_PATTERN.fullmatch(email):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That is not an email address.",
        )
    return email


def _unavailable(exc: SQLAlchemyError) -> HTTPException:
    logger.error("Password sign-in could not reach the database: %s", exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Accounts are unavailable right now. Please try again shortly.",
    )


def link_email(to: str, url: str, link: EmailLink) -> Email:
    """The email carrying ``url``, worded for an address with or without an account.

    Only the reader of the address sees which it is, since the page that asked says the
    same either way.
    """
    if link.has_password:
        subject = "Reset your PyQxel password"
        lead = "Someone asked to reset the password of the PyQxel account at this address."
        action = "Choose a new password"
    elif link.has_account:
        subject = "Add a password to your PyQxel account"
        lead = (
            "Someone asked to add a password to the PyQxel account at this address, so it "
            "can sign in with an email and password as well as the way it does now."
        )
        action = "Choose a password"
    else:
        subject = "Finish making your PyQxel account"
        lead = "Someone asked to make a PyQxel account with this address."
        action = "Choose a password"
    ignore = (
        "If that was not you, ignore this email: nothing changes unless the link is used, "
        f"and it stops working in {EMAIL_LINK_MINUTES} minutes. Never forward it, since "
        "whoever opens it can choose the password."
    )
    text = f"{lead}\n\n{action}: {url}\n\n{ignore}\n\nPyQxel"
    safe_url = html.escape(url, quote=True)
    body = (
        f"<p>{html.escape(lead)}</p>"
        f'<p><a href="{safe_url}" style="display:inline-block;padding:10px 16px;'
        "border-radius:8px;background:#2a78d6;color:#ffffff;font-weight:600;"
        f'text-decoration:none">{html.escape(action)}</a></p>'
        f"<p>Or open this address: {safe_url}</p>"
        f'<p style="color:#52514e">{html.escape(ignore)}</p><p>PyQxel</p>'
    )
    return Email(to=to, subject=subject, text=text, html=body)


async def _send(sender: EmailSender, email: Email) -> None:
    """Send ``email`` after the answer has gone, so how long sending takes, or whether
    it fails, says nothing about the address to whoever asked."""
    try:
        await sender.send(email)
    except EmailError as exc:
        logger.error("Could not send a sign-in email: %s", exc)


def offered(settings: Settings) -> bool:
    """Whether this server can send the emails password sign-in needs."""
    return email_sender(settings) is not None and site_url(settings) is not None


@router.get("", response_model=PasswordSignInInfo, summary="Password sign-in offered")
async def password_sign_in_info() -> PasswordSignInInfo:
    """Whether people can sign in with an email and password here, and the shortest
    password allowed."""
    return PasswordSignInInfo(available=offered(get_settings()), min_length=MIN_PASSWORD_LENGTH)


@router.post(
    "/link",
    response_model=LinkSent,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email a link to choose a password",
)
async def send_password_link(
    body: EmailAddress, request: Request, session: Session, tasks: BackgroundTasks
) -> LinkSent:
    """Email ``email`` a link to choose a password, which makes the account if the
    address has none. The answer is the same either way.

    Returns 404 when this server cannot send email, 422 for something that is not an
    email address, 429 beyond 20 links an hour from one client, three to one address,
    or the server's daily number, and 503 when accounts are unavailable.
    """
    sender, site = _sender()
    email = _email(body.email)
    try:
        await check_rate_limit(f"password-link:{_address(request)}", LINKS_PER_ADDRESS, None, HOUR)
        await check_rate_limit(f"password-link:{email}", LINKS_PER_EMAIL, None, HOUR)
        await check_rate_limit("password-link:all", LINKS_PER_DAY, None, 24 * HOUR)
    except RateLimitExceeded as exc:
        raise _too_many(exc, "Too many emails asked for; please try again later.") from exc
    try:
        link = await new_email_link(session, email)
    except SQLAlchemyError as exc:
        await session.rollback()
        raise _unavailable(exc) from exc
    # The token goes in the fragment, which browsers never send to a server, so it is
    # not written to logs, and a mail scanner that opens the link cannot use it up.
    url = f"{site}/signin/password#token={link.token}"
    tasks.add_task(_send, sender, link_email(email, url, link))
    return LinkSent(
        message=f"We sent a link to {email}. Open it within {EMAIL_LINK_MINUTES} minutes to "
        "choose your password. If it does not arrive, check your spam folder."
    )


@router.post("/check", response_model=LinkAddress, summary="Check an emailed link")
async def check_password_link(body: LinkToken, request: Request, session: Session) -> LinkAddress:
    """The address an emailed link was sent to, so its page can name the account,
    without using the link up.

    Returns 400 for a link that is unknown, used, or expired, 429 beyond the sign-in
    attempts one client may make a minute, and 503 when accounts are unavailable.
    """
    try:
        await check_rate_limit(f"password:{_address(request)}", ATTEMPTS_PER_ADDRESS)
    except RateLimitExceeded as exc:
        raise _too_many(exc, "Too many attempts; please wait a minute.") from exc
    try:
        email = await email_for_link(session, body.token)
    except SQLAlchemyError as exc:
        raise _unavailable(exc) from exc
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That link has expired or was already used. Ask for a new one.",
        )
    return LinkAddress(email=email)


@router.post("/set", response_model=PasswordSignedIn, summary="Choose a password")
async def set_password(body: NewPassword, request: Request, session: Session) -> PasswordSignedIn:
    """Set the password of the account an emailed link was sent to, making the account
    if there is none, and sign this browser in. Replacing a password signs out every
    device that signed in with the old one.

    Returns 400 for a link that is unknown, used, or expired, 422 for a password too
    short, too long, or too easy to guess (the link still works then), 429 beyond the
    attempts one client may make a minute, and 503 when accounts are unavailable.
    """
    try:
        await check_rate_limit(f"password:{_address(request)}", ATTEMPTS_PER_ADDRESS)
    except RateLimitExceeded as exc:
        raise _too_many(exc, "Too many attempts; please wait a minute.") from exc
    try:
        email = await email_for_link(session, body.token)
        if email is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That link has expired or was already used. Ask for a new one.",
            )
        # Checked before the link is used up, so a refused password can be retried.
        problem = password_problem(body.password, email)
        if problem:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)
        result = await use_email_link(session, body.token, body.password)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That link has expired or was already used. Ask for a new one.",
            )
        issued = await create_api_key(
            session,
            result.user.id,
            "Email sign-in",
            signed_in_with=PASSWORD_SIGN_IN,
            expires_after=timedelta(days=get_settings().sign_in_key_days),
        )
    except SQLAlchemyError as exc:
        await session.rollback()
        raise _unavailable(exc) from exc
    return PasswordSignedIn(key=issued.key, email=result.user.email)


@router.post("/sign-in", response_model=PasswordSignedIn, summary="Sign in with a password")
async def sign_in_with_password(
    body: PasswordSignIn, request: Request, session: Session
) -> PasswordSignedIn:
    """Sign in with an email and password and get an API key, which expires after
    ``SIGN_IN_KEY_DAYS`` and is revoked by signing out.

    Returns 401 when the email and password do not match an account (the same for an
    unknown address), 404 when this server does not offer password sign-in, 429 beyond
    30 attempts a minute from one client, ten in 15 minutes at one account or the
    server's limit, and 503 when accounts are unavailable.
    """
    _sender()
    email = normalize_email(body.email)
    try:
        await check_rate_limit(f"password:{_address(request)}", ATTEMPTS_PER_ADDRESS)
        await check_rate_limit(
            f"password-account:{email}", ATTEMPTS_PER_ACCOUNT, None, ACCOUNT_WINDOW_SECONDS
        )
        await check_rate_limit("password:all", ATTEMPTS_PER_SERVER)
    except RateLimitExceeded as exc:
        raise _too_many(
            exc,
            "Too many sign-in attempts; please wait, or use Forgot password to get a link "
            "by email.",
        ) from exc
    try:
        user = await user_for_password(session, email, body.password)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="That email and password do not match an account. Check them, or "
                "use Forgot password to choose a new one.",
            )
        issued = await create_api_key(
            session,
            user.id,
            "Email sign-in (app)" if body.app else "Email sign-in",
            signed_in_with=PASSWORD_SIGN_IN,
            expires_after=timedelta(days=get_settings().sign_in_key_days),
        )
    except SQLAlchemyError as exc:
        await session.rollback()
        raise _unavailable(exc) from exc
    return PasswordSignedIn(key=issued.key, email=user.email)
