"""Signing in with GitHub or Google, and signing out.

These routes come before an account has an API key, so they are not behind the API key
check. A completed sign-in issues the browser an API key, as the rest of the API expects,
marked as a sign-in key so that signing out revokes it.
"""

import base64
import logging
import secrets
from datetime import timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import presented_key
from app.core.config import get_settings
from app.core.oauth import (
    OAuthError,
    OAuthProvider,
    ProviderName,
    configured_providers,
    new_secret,
)
from app.db.session import get_session
from app.db.users import create_api_key, revoke_signed_in_key, user_for_identity

logger = logging.getLogger(__name__)

router = APIRouter()
"""Routes under ``/auth``."""

Session = Annotated[AsyncSession, Depends(get_session)]

COOKIE = "pyqxel_sign_in"
"""Holds a sign-in's state, PKCE verifier and destination while the provider is visited."""
COOKIE_SECONDS = 10 * 60


class SignInProvider(BaseModel):
    """A provider people can sign in with on this server."""

    name: ProviderName
    label: str


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


@router.get("/providers", response_model=list[SignInProvider], summary="Ways to sign in")
async def sign_in_providers() -> list[SignInProvider]:
    """The providers people can sign in with here, in the order to show them."""
    return [
        SignInProvider(name=provider.name, label=provider.label)
        for provider in configured_providers(get_settings()).values()
    ]


@router.get("/{provider}/start", summary="Start signing in", include_in_schema=False)
async def start_sign_in(
    provider: ProviderName, request: Request, next: str = "/"
) -> RedirectResponse:
    """Send the browser to ``provider`` to sign in, returning to ``next`` afterwards."""
    oauth = _provider(provider)
    state, verifier = new_secret(), new_secret()
    response = RedirectResponse(
        oauth.authorization_url(_callback_url(request, provider), state, verifier),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.set_cookie(
        COOKIE,
        ".".join([state, verifier, _encode(_destination(next))]),
        max_age=COOKIE_SECONDS,
        path=_cookie_path(),
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )
    return response


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
) -> RedirectResponse:
    """Finish a sign-in the provider sent back, and give the browser an API key.

    The key, or the reason signing in failed, goes back to the sign-in page in the URL
    fragment: ``#key=…&next=…`` or ``#error=…``.
    """
    oauth = _provider(provider)
    if error:
        message = (
            f"Signing in with {oauth.label} was cancelled."
            if error == "access_denied"
            else f"{oauth.label} did not complete the sign-in: {error}."
        )
        return _back_to_sign_in(request, error=message)
    parts = request.cookies.get(COOKIE, "").split(".")
    if len(parts) != 3 or not code or not state or not secrets.compare_digest(parts[0], state):
        return _back_to_sign_in(
            request,
            error="That sign-in expired or was started in another browser. Please try again.",
        )
    sent_state, verifier, destination = parts
    try:
        identity = await oauth.identity(code, _callback_url(request, provider), verifier)
    except OAuthError as exc:
        return _back_to_sign_in(request, error=str(exc))
    try:
        user = await user_for_identity(session, provider, identity.subject, identity.email)
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
        return _back_to_sign_in(
            request, error="Accounts are unavailable right now. Please try again shortly."
        )
    return _back_to_sign_in(request, key=issued.key, next=_destination(_decode(destination)))


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
