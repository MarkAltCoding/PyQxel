"""The authenticated user's account: provider keys, AI limits, PyQxel API keys, and
deleting the account.

Each account pays for its own usage. AI analyses run on the Anthropic key stored here,
market data on the Financial Modeling Prep key, and data the other sources leave out on
the Alpha Vantage key; none is ever used for another account, and the server's own keys
are never used for anyone.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import CurrentUser
from app.core.config import get_settings
from app.core.security import CredentialsUnavailableError
from app.db.session import get_session
from app.db.users import (
    User,
    ai_usage,
    create_api_key,
    delete_user,
    list_api_keys,
    revoke_api_key,
    update_credentials,
    update_limits,
)
from app.models.account import (
    Account,
    AiLimits,
    ApiKeyCreate,
    ApiKeyInfo,
    CredentialsUpdate,
    NewApiKey,
    ProviderCredential,
)

logger = logging.getLogger(__name__)

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]


def _database_error(exc: SQLAlchemyError) -> HTTPException:
    logger.error("Account database request failed: %s", exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="The account database is unavailable.",
    )


async def _account(session: AsyncSession, user: User) -> Account:
    return Account(
        id=UUID(user.id),
        email=user.email,
        created_at=user.created_at,
        anthropic=ProviderCredential(
            configured=user.anthropic_api_key is not None, hint=user.anthropic_key_hint
        ),
        fmp=ProviderCredential(configured=user.fmp_api_key is not None, hint=user.fmp_key_hint),
        alpha_vantage=ProviderCredential(
            configured=user.alpha_vantage_api_key is not None, hint=user.alpha_vantage_key_hint
        ),
        limits=user.limits,
        usage=await ai_usage(session, user.id),
        rate_limit_per_minute=get_settings().rate_limit_per_minute,
    )


@router.get("", response_model=Account, summary="Your account")
async def read_account(user: CurrentUser, session: Session) -> Account:
    """Return the account: which provider keys are stored, AI limits and recent usage."""
    try:
        return await _account(session, user)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@router.put("/credentials", response_model=Account, summary="Store your provider keys")
async def store_credentials(
    user: CurrentUser, session: Session, changes: Annotated[CredentialsUpdate, Body()]
) -> Account:
    """Store, replace or remove your Anthropic, Financial Modeling Prep and Alpha Vantage keys.

    Omitted fields are left as they are and ``null`` removes a key. Keys are encrypted at
    rest, never returned, and used only for your own requests: analyses are billed to
    your Anthropic account, market data to your FMP plan, and the gaps Alpha Vantage
    fills to your Alpha Vantage plan.

    Returns 503 when the server has no ``CREDENTIALS_ENCRYPTION_KEY``.
    """
    try:
        updated = await update_credentials(session, user.id, changes)
        return await _account(session, updated)
    except CredentialsUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@router.put("/limits", response_model=Account, summary="Set your AI request limits")
async def store_limits(
    user: CurrentUser, session: Session, limits: Annotated[AiLimits, Body()]
) -> Account:
    """Cap how many new AI analyses may be written for you per hour and per day.

    They are billed to your own Anthropic key, so the limits are yours to choose; ``null``
    removes a cap. Analyses beyond a cap are refused with 429 until the window passes.
    """
    try:
        updated = await update_limits(session, user.id, limits)
        return await _account(session, updated)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@router.get("/api-keys", response_model=list[ApiKeyInfo], summary="List your API keys")
async def read_api_keys(user: CurrentUser, session: Session) -> list[ApiKeyInfo]:
    """Return your PyQxel API keys, oldest first, without the keys themselves."""
    try:
        return await list_api_keys(session, user.id)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@router.post(
    "/api-keys",
    response_model=NewApiKey,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key",
)
async def add_api_key(
    user: CurrentUser, session: Session, request: Annotated[ApiKeyCreate, Body()]
) -> NewApiKey:
    """Create another PyQxel API key, e.g. one per device. The key is shown only now."""
    try:
        return await create_api_key(session, user.id, request.name)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@router.delete(
    "/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Revoke an API key",
)
async def remove_api_key(key_id: UUID, user: CurrentUser, session: Session) -> Response:
    """Revoke one of your API keys; requests with it are refused from now on.

    Returns 404 for keys that are not yours.
    """
    try:
        revoked = await revoke_api_key(session, user.id, key_id)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such API key.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete your account",
)
async def delete_account(user: CurrentUser, session: Session) -> Response:
    """Delete your account and everything it holds, at once and for good.

    Your provider keys, PyQxel API keys, sign-in links to GitHub and Google, holdings,
    and stored backtests, analyses and simulations are deleted, and every API key stops
    working. Signing in again afterwards starts a new, empty account.
    """
    try:
        await delete_user(session, user.id)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    logger.info("Deleted account %s at its owner's request.", user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
