"""Authentication of API requests by PyQxel API key, and per-user rate limiting.

Clients send their key as ``Authorization: Bearer <key>`` or ``X-API-Key: <key>``.
Browsers cannot set headers on WebSocket connections, so the quote stream also accepts
``?api_key=<key>``; HTTP routes do not, to keep keys out of URLs and logs.

Authenticating a request also makes the user's own provider keys the ones its market
data is fetched with (see :mod:`app.core.credentials`).
"""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, WebSocketException, status
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import HTTPConnection

from app.core.config import get_settings
from app.core.credentials import ProviderKeys, use_provider_keys
from app.core.rate_limit import RateLimitExceeded, check_rate_limit
from app.core.security import CredentialsUnavailableError
from app.db.session import get_sessionmaker
from app.db.users import User
from app.db.users import authenticate as user_for_key

logger = logging.getLogger(__name__)


def _presented_key(connection: HTTPConnection) -> str | None:
    """The API key the client sent, if any."""
    authorization = connection.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    if header := connection.headers.get("x-api-key", "").strip():
        return header
    if connection.scope["type"] == "websocket":
        return connection.query_params.get("api_key") or None
    return None


def _refuse(
    connection: HTTPConnection, status_code: int, detail: str, headers: dict[str, str] | None = None
) -> Exception:
    """An HTTP error, or the WebSocket close that stands in for one."""
    if connection.scope["type"] == "websocket":
        return WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason=detail)
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


async def authenticate(connection: HTTPConnection) -> User:
    """Identify the user by API key, count the request against their rate limit, and
    make their provider keys the request's.

    Raises:
        HTTPException: 401 for a missing, unknown or revoked key; 429 beyond the rate
            limit; 503 when the account database or stored credentials are unavailable.
            WebSocket connections are closed with code 1008 instead.
    """
    key = _presented_key(connection)
    if key is None:
        raise _refuse(
            connection,
            status.HTTP_401_UNAUTHORIZED,
            "Send your PyQxel API key as 'Authorization: Bearer <key>'.",
            {"WWW-Authenticate": "Bearer"},
        )
    try:
        async with get_sessionmaker()() as session:
            user = await user_for_key(session, key)
    except SQLAlchemyError as exc:
        logger.error("Could not check an API key: %s", exc)
        raise _refuse(
            connection, status.HTTP_503_SERVICE_UNAVAILABLE, "The account database is unavailable."
        ) from exc
    if user is None:
        raise _refuse(
            connection,
            status.HTTP_401_UNAUTHORIZED,
            "The API key is unknown or revoked.",
            {"WWW-Authenticate": "Bearer"},
        )

    try:
        await check_rate_limit(user.id, get_settings().rate_limit_per_minute)
    except RateLimitExceeded as exc:
        raise _refuse(
            connection,
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests; slow down.",
            {"Retry-After": str(exc.retry_after)},
        ) from exc

    try:
        use_provider_keys(ProviderKeys(fmp=user.fmp_key()))
    except CredentialsUnavailableError as exc:
        logger.error("Cannot read the stored credentials of user %s: %s", user.id, exc)
        raise _refuse(connection, status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return user


CurrentUser = Annotated[User, Depends(authenticate)]
"""The authenticated user; FastAPI resolves it once per request however often it is used."""
