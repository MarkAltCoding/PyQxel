"""Signing in with GitHub, Google or Apple, and signing out.

These routes come before an account has an API key, so they are not behind the API key
check. A completed sign-in issues the browser an API key, as the rest of the API expects,
marked as a sign-in key so that signing out revokes it.

The native app signs in through the system browser, since providers refuse sign-ins in
an app's own web view and the browser already knows the person. It starts the sign-in
with the SHA-256 challenge of a secret verifier it keeps. When the sign-in finishes, the
browser is handed a one-time code in a ``pyqxel://signin`` link, which opens the app;
the app then trades the code and its verifier for an API key. Another app that claimed
the link would hold only the code, which is useless without the verifier.
"""

import base64
import html
import logging
import re
import secrets
from datetime import timedelta
from typing import Annotated
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import presented_key
from app.core.rate_limit import RateLimitExceeded, check_rate_limit
from app.core.config import get_settings
from app.core.oauth import (
    OAuthError,
    PROVIDER_LABELS,
    OAuthProvider,
    ProviderName,
    configured_providers,
    new_secret,
)
from app.db.session import get_session
from app.db.users import (
    authenticate,
    collect_app_sign_in,
    create_api_key,
    hold_app_sign_in,
    renew_signed_in_key,
    revoke_signed_in_key,
    user_for_identity,
)

logger = logging.getLogger(__name__)

router = APIRouter()
"""Routes under ``/auth``."""

Session = Annotated[AsyncSession, Depends(get_session)]

COOKIE = "pyqxel_sign_in"
"""Holds a sign-in's state, PKCE verifier, destination and, for the native app, its
challenge while the provider is visited."""
COOKIE_SECONDS = 10 * 60

APP_LINK = "pyqxel://signin"
"""The link that opens the native app's sign-in page, registered by the app."""
APP_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
"""A PKCE S256 challenge: a SHA-256 digest in unpadded URL-safe base64."""

SIGN_INS_PER_ADDRESS: int = 20
"""Sign-in starts and callbacks one client address may make a minute."""
SIGN_INS_PER_SERVER: int = 600
"""Sign-in starts and callbacks the whole server takes a minute, which bounds the work
an attacker can cause from however many addresses."""


class SignInProvider(BaseModel):
    """A provider people can sign in with on this server."""

    name: ProviderName
    label: str


class AppSignInCollect(BaseModel):
    """What the native app trades for its API key after signing in in the browser."""

    code: str = Field(min_length=1, max_length=128, description="The link's one-time code.")
    verifier: str = Field(
        min_length=43,
        max_length=128,
        description="The secret whose SHA-256 challenge the app started the sign-in with.",
    )


class RenewedKey(BaseModel):
    """A sign-in key renewed for another full term, or none when it is not due."""

    key: str | None = Field(
        description="The new API key, to use in place of the old one from now on, or null "
        "when the key is less than a day old, or was not issued by signing in."
    )


class AppSignInKey(BaseModel):
    """The API key a sign-in issued the native app, and the page to open."""

    key: str
    next: str


def _cookie_path() -> str:
    return f"{get_settings().api_v1_prefix}/auth"


def _provider(name: ProviderName) -> OAuthProvider:
    """The configured provider, or a 404 when this server has no client for it."""
    provider = configured_providers(get_settings()).get(name)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Signing in with {name} is not set up on this server.",
        )
    return provider


def _destination(path: str | None) -> str:
    """``path`` if it is a page on this site, else the home page, so a sign-in link
    cannot send people on to another site."""
    if path and path.startswith("/") and not path.startswith("//") and "\\" not in path:
        return path
    return "/"


async def _throttle(request: Request) -> None:
    """Count a sign-in request, from its address and in all.

    Raises:
        RateLimitExceeded: Beyond either limit this minute.
    """
    address = request.client.host if request.client else "unknown"
    await check_rate_limit(f"sign-in:{address}", SIGN_INS_PER_ADDRESS)
    await check_rate_limit("sign-in:all", SIGN_INS_PER_SERVER)


def _too_many(exc: RateLimitExceeded) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many sign-in attempts; please wait a minute.",
        headers={"Retry-After": str(exc.retry_after)},
    )


def _encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _decode(text: str) -> str:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode()


def _callback_url(request: Request, provider: ProviderName) -> str:
    """Where the provider sends the browser back; registered with the provider as is."""
    return str(request.url_for("finish_sign_in", provider=provider))


def _back_to_sign_in(request: Request, **fragment: str) -> RedirectResponse:
    """Send the browser to the sign-in page with ``fragment``, which browsers never send
    to a server, and drop the sign-in cookie."""
    response = RedirectResponse(
        f"/signin#{urlencode(fragment)}", status_code=status.HTTP_303_SEE_OTHER
    )
    response.delete_cookie(COOKIE, path=_cookie_path())
    return response


def _back_to_app(**fragment: str) -> HTMLResponse:
    """A page that opens the native app's sign-in page with ``fragment``, and says to
    return to the app if the browser does not open it, and drops the sign-in cookie.

    A page rather than a redirect, so the browser tab is left saying what happened
    instead of blank, with a button to press where a browser asks before opening an app.
    """
    link = html.escape(f"{APP_LINK}#{urlencode(fragment)}")
    failed = "error" in fragment
    if failed:
        heading = "Signing in did not finish"
        message = f"{html.escape(fragment['error'])} Return to PyQxel to try again."
        button = "Return to PyQxel"
    else:
        heading = "You are signed in"
        message = "PyQxel should open by itself. If it does not, open it with the button below."
        button = "Open PyQxel"
    page = _APP_PAGE.format(
        link=link,
        heading=heading,
        message=message,
        button=button,
        status="failed" if failed else "done",
        mark="!" if failed else "&#10003;",
    )
    response = HTMLResponse(page, headers=APP_PAGE_HEADERS)
    response.delete_cookie(COOKIE, path=_cookie_path())
    return response


APP_PAGE_HEADERS: dict[str, str] = {
    # It carries a one-time code, so it is neither kept nor passed on.
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    # Its own styles only: no script, nothing loaded from elsewhere.
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
}
"""Headers of the page that hands a sign-in back to the native app."""

_APP_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta http-equiv="refresh" content="0;url={link}">
<title>{heading} · PyQxel</title>
<style>
  :root {{
    --page: #f9f9f7; --surface: #ffffff; --text: #0b0b0b; --muted: #52514e;
    --border: rgba(11, 11, 11, 0.1); --accent: #2a78d6; --accent-hover: #256abf;
    --accent-text: #ffffff; --done: #e6f0fc; --failed: #fdeceb; --failed-text: #b3261e;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --page: #0d0d0d; --surface: #1a1a19; --text: #f0efec; --muted: #c3c2b7;
      --border: rgba(255, 255, 255, 0.1); --accent: #3987e5; --accent-hover: #5598e7;
      --accent-text: #0b0b0b; --done: #17283d; --failed: #3a1d1b; --failed-text: #f2b8b5;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px 16px;
    background: var(--page); color: var(--text);
    font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  }}
  main {{
    width: min(420px, 100%); padding: 36px 32px 32px; text-align: center;
    background: var(--surface); border: 1px solid var(--border); border-radius: 16px;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04), 0 8px 24px rgba(0, 0, 0, 0.06);
  }}
  .brand {{
    display: inline-flex; align-items: center; gap: 8px; margin-bottom: 28px;
    font-weight: 600; font-size: 15px; color: var(--muted);
  }}
  .brand svg {{ width: 24px; height: 24px; }}
  .status {{
    width: 56px; height: 56px; margin: 0 auto 16px; border-radius: 50%;
    display: grid; place-items: center; font-size: 26px; font-weight: 700;
    background: var(--done); color: var(--accent);
  }}
  .status.failed {{ background: var(--failed); color: var(--failed-text); }}
  h1 {{ margin: 0 0 8px; font-size: 22px; line-height: 1.3; letter-spacing: -0.01em; }}
  p {{ margin: 0; color: var(--muted); }}
  .button {{
    display: block; margin: 24px 0 12px; padding: 11px 16px; border-radius: 10px;
    background: var(--accent); color: var(--accent-text); font-weight: 600;
    text-decoration: none;
  }}
  .button:hover {{ background: var(--accent-hover); }}
  .button:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 3px; }}
  .note {{ font-size: 14px; }}
</style>
</head>
<body>
<main>
  <div class="brand">
    <svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7"
      fill="#2a78d6"/><path d="M7 22l6-7 5 4 7-9" fill="none" stroke="#fff"
      stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
    PyQxel
  </div>
  <div class="status {status}" aria-hidden="true">{mark}</div>
  <h1>{heading}</h1>
  <p>{message}</p>
  <a class="button" href="{link}">{button}</a>
  <p class="note">You can close this tab afterwards.</p>
</main>
</body>
</html>
"""
"""The page itself; ``{link}`` and every other value are escaped before they go in."""


def _back(request: Request, challenge: str, **fragment: str) -> Response:
    """Return what a sign-in came to, to the native app when ``challenge`` says the app
    started it, otherwise to the website's sign-in page."""
    if challenge:
        return _back_to_app(**fragment)
    return _back_to_sign_in(request, **fragment)


@router.get("/providers", response_model=list[SignInProvider], summary="Ways to sign in")
async def sign_in_providers() -> list[SignInProvider]:
    """The providers people can sign in with here, in the order to show them."""
    return [
        SignInProvider(name=provider.name, label=provider.label)
        for provider in configured_providers(get_settings()).values()
    ]


@router.get("/{provider}/start", summary="Start signing in", include_in_schema=False)
async def start_sign_in(
    provider: ProviderName, request: Request, next: str = "/", app: str | None = None
) -> RedirectResponse:
    """Send the browser to ``provider`` to sign in, returning to ``next`` afterwards.

    ``app`` is the native app's PKCE challenge when the app started the sign-in, which
    sends it back to the app instead of the website.
    """
    oauth = _provider(provider)
    if app is not None and not APP_CHALLENGE.fullmatch(app):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The app's sign-in challenge is not valid; please update the app.",
        )
    try:
        await _throttle(request)
    except RateLimitExceeded as exc:
        raise _too_many(exc) from exc
    state, verifier = new_secret(), new_secret()
    response = RedirectResponse(
        oauth.authorization_url(_callback_url(request, provider), state, verifier),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    secure = request.url.scheme == "https"
    response.set_cookie(
        COOKIE,
        ".".join([state, verifier, _encode(_destination(next)), app or ""]),
        max_age=COOKIE_SECONDS,
        path=_cookie_path(),
        httponly=True,
        secure=secure,
        # A provider that posts its answer back is a cross-site request, which carries
        # only cookies allowed on those; such cookies must be secure.
        samesite="none" if oauth.posts_back and secure else "lax",
    )
    return response


def _cookie_parts(request: Request) -> tuple[str, str, str, str] | None:
    """The sign-in cookie's state, PKCE verifier, encoded destination and app challenge
    (empty for the website), or ``None`` without a well-formed cookie."""
    parts = request.cookies.get(COOKIE, "").split(".")
    if len(parts) == 3:
        parts.append("")
    if len(parts) != 4 or not all(parts[:3]):
        return None
    return parts[0], parts[1], parts[2], parts[3]


@router.get(
    "/{provider}/callback",
    name="finish_sign_in",
    summary="Finish signing in",
    include_in_schema=False,
)
async def finish_sign_in(
    provider: ProviderName,
    request: Request,
    session: Session,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Finish a sign-in the provider sent back, and give the browser an API key.

    The key, or the reason signing in failed, goes back to the sign-in page in the URL
    fragment: ``#key=…&next=…`` or ``#error=…``. A sign-in the native app started goes
    back to the app instead, with a one-time code in place of the key.
    """
    return await _finish(provider, request, session, code, state, error)


@router.post("/{provider}/callback", summary="Finish signing in", include_in_schema=False)
async def finish_posted_sign_in(
    provider: ProviderName, request: Request, session: Session
) -> Response:
    """Finish a sign-in a provider posted back as a form, as Apple does."""
    body = (await request.body()).decode(errors="replace")
    form = {name: values[0] for name, values in parse_qs(body).items()}
    return await _finish(
        provider, request, session, form.get("code"), form.get("state"), form.get("error")
    )


async def _finish(
    provider: ProviderName,
    request: Request,
    session: Session,
    code: str | None,
    state: str | None,
    error: str | None,
) -> Response:
    """Check what the provider sent back against the sign-in cookie, find or make the
    account, and send the browser on with its key, or the app with a code for one."""
    oauth = _provider(provider)
    parts = _cookie_parts(request)
    challenge = parts[3] if parts else ""
    try:
        await _throttle(request)
    except RateLimitExceeded:
        return _back(
            request,
            challenge,
            error="Too many sign-in attempts; please wait a minute and try again.",
        )
    if error:
        # The error comes from the URL, which anyone can write, so it is not shown.
        message = (
            f"Signing in with {oauth.label} was cancelled."
            if error in ("access_denied", "user_cancelled_authorize")
            else f"{oauth.label} did not complete the sign-in. Please try again."
        )
        return _back(request, challenge, error=message)
    if parts is None or not code or not state or not secrets.compare_digest(parts[0], state):
        return _back(
            request,
            challenge,
            error="That sign-in expired or was started in another browser. Please try again.",
        )
    _, verifier, destination, _ = parts
    try:
        identity = await oauth.identity(code, _callback_url(request, provider), verifier)
    except OAuthError as exc:
        return _back(request, challenge, error=str(exc))
    next_page = _destination(_decode(destination))
    try:
        user = await user_for_identity(session, provider, identity.subject, identity.email)
        if challenge:
            handoff = await hold_app_sign_in(session, user.id, provider, challenge, next_page)
            return _back_to_app(code=handoff, next=next_page)
        issued = await create_api_key(
            session,
            user.id,
            f"{oauth.label} sign-in",
            signed_in_with=provider,
            expires_after=timedelta(days=get_settings().sign_in_key_days),
        )
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.error("Could not sign in with %s: %s", provider, exc)
        return _back(
            request,
            challenge,
            error="Accounts are unavailable right now. Please try again shortly.",
        )
    return _back_to_sign_in(request, key=issued.key, next=next_page)


@router.post(
    "/app/collect",
    response_model=AppSignInKey,
    summary="Collect the native app's sign-in",
)
async def collect_sign_in(
    body: AppSignInCollect, request: Request, session: Session
) -> AppSignInKey:
    """Trade the one-time code a sign-in handed the native app, with the verifier the app
    started it with, for an API key. Each code works once, within five minutes."""
    try:
        await _throttle(request)
    except RateLimitExceeded as exc:
        raise _too_many(exc) from exc
    try:
        collected = await collect_app_sign_in(session, body.code, body.verifier)
        if collected is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That sign-in expired or was already used. Please sign in again.",
            )
        issued = await create_api_key(
            session,
            collected.user_id,
            f"{PROVIDER_LABELS.get(collected.provider, collected.provider)} sign-in (app)",
            signed_in_with=collected.provider,
            expires_after=timedelta(days=get_settings().sign_in_key_days),
        )
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.error("Could not collect an app sign-in: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Accounts are unavailable right now. Please try again shortly.",
        ) from exc
    return AppSignInKey(key=issued.key, next=collected.destination)


@router.post("/renew", response_model=RenewedKey, summary="Renew a sign-in key")
async def renew_sign_in(request: Request, session: Session) -> RenewedKey:
    """Trade the sign-in key this request carries, if it is more than a day old, for a
    new one good for ``SIGN_IN_KEY_DAYS``, so someone who keeps using PyQxel stays signed
    in. The old key works for five more minutes. Keys made for scripts are never renewed.

    Returns 401 without a working key, 429 beyond the sign-in attempts one client may
    make a minute, and 503 when accounts are unavailable.
    """
    key = presented_key(request)
    try:
        await _throttle(request)
    except RateLimitExceeded as exc:
        raise _too_many(exc) from exc
    try:
        if not key or await authenticate(session, key) is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sign in again: this key has expired or was revoked.",
            )
        renewed = await renew_signed_in_key(
            session, key, timedelta(days=get_settings().sign_in_key_days)
        )
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.error("Could not renew a sign-in key: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Accounts are unavailable right now. Please try again shortly.",
        ) from exc
    return RenewedKey(key=None if renewed is None else renewed.key)


@router.post(
    "/signout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Sign out",
)
async def sign_out(request: Request, session: Session) -> Response:
    """Revoke the API key this request carries if a sign-in issued it. Keys created for
    scripts keep working, so signing out of the website does not break them."""
    key = presented_key(request)
    if key:
        try:
            await revoke_signed_in_key(session, key)
        except SQLAlchemyError as exc:
            await session.rollback()
            logger.error("Could not revoke a signed-in key: %s", exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
