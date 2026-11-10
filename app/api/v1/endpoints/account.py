"""The authenticated user's account: AI limits, PyQxel API keys, admin settings, and
deleting the account.

Each account pays for its own usage, with provider keys PyQxel never stores: the browser
keeps them and sends them with each request (see :mod:`app.core.credentials`), so AI
analyses run on the Anthropic key the request carries, market data on its Financial
Modeling Prep key, the gaps on its Alpha Vantage key, and news on its Finnhub key.

The one exception is the nightly screener refresh, which runs while no browser is open: a
user who opts in has their market data keys stored, encrypted, until they opt out or
delete the account (see :mod:`app.db.nightly`).
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import Match

import stripe
from app.api.auth import CurrentUser, authenticate, uses_restricted_sources, uses_yfinance
from app.billing import stripe_api
from app.core.config import get_settings
from app.core.credentials import ProviderKeys, current_provider_keys
from app.core.security import CredentialsUnavailableError, encryption_available, key_hint
from app.db import billing
from app.db.app_settings import (
    STYLE_FACTORS,
    RESTRICTED_SOURCE_USES,
    RESTRICTED_SOURCES,
    server_offers_style_factors,
    server_uses_restricted_sources,
    set_setting,
)
from app.db.nightly import (
    MissingFinnhubKeyError,
    UniverseChoice,
    disable,
    enable,
    refresh_status,
)
from app.db.session import get_session
from app.db.users import (
    User,
    ai_usage,
    create_api_key,
    delete_user,
    list_api_keys,
    revoke_api_key,
    update_limits,
    update_own_restricted_sources,
    update_yfinance_fallback,
)
from app.models.account import (
    Account,
    AdminSettingsUpdate,
    AiLimits,
    ApiKeyCreate,
    ApiKeyInfo,
    NewApiKey,
    ProviderCredential,
)
from app.models.nightly import (
    EVERYTHING,
    MAX_CUSTOM_SYMBOLS,
    NightlyProvider,
    NightlyRefreshSettings,
    NightlyRefreshStatus,
    NightlyUniverse,
)

logger = logging.getLogger(__name__)

router = APIRouter()

ALL_METHODS: list[str] = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

Session = Annotated[AsyncSession, Depends(get_session)]


def _database_error(exc: SQLAlchemyError) -> HTTPException:
    logger.error("Account database request failed: %s", exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="The account database is unavailable.",
    )


def _sent(keys: ProviderKeys, provider: str) -> ProviderCredential:
    """Whether the request carried ``provider``'s key, and its last characters."""
    secret = getattr(keys, provider)
    if secret is None:
        return ProviderCredential(configured=False)
    return ProviderCredential(configured=True, hint=key_hint(secret.get_secret_value()))


async def _account(session: AsyncSession, user: User) -> Account:
    keys = current_provider_keys()
    return Account(
        id=UUID(user.id),
        email=user.email,
        created_at=user.created_at,
        anthropic=_sent(keys, "anthropic"),
        fmp=_sent(keys, "fmp"),
        alpha_vantage=_sent(keys, "alpha_vantage"),
        finnhub=_sent(keys, "finnhub"),
        tiingo=_sent(keys, "tiingo"),
        eodhd=_sent(keys, "eodhd"),
        limits=user.limits,
        usage=await ai_usage(session, user.id),
        rate_limit_per_minute=get_settings().rate_limit_per_minute,
        market_data_fallback=uses_yfinance(user),
        sec_filings=bool(get_settings().sec_user_agent),
        style_factors=(
            uses_restricted_sources(user)
            or await server_uses_restricted_sources()
            or await server_offers_style_factors()
        ),
        admin=(
            {
                "yfinance_fallback": user.yfinance_fallback,
                "yfinance_fallback_from_server": get_settings().yfinance_fallback,
                "restricted_sources": await server_uses_restricted_sources(),
                "own_restricted_sources": user.own_restricted_sources,
                "style_factors": await server_offers_style_factors(),
                "restricted_source_uses": list(RESTRICTED_SOURCE_USES),
            }
            if user.is_admin
            else None
        ),
    )


@router.get("", response_model=Account, summary="Your account")
async def read_account(user: CurrentUser, session: Session) -> Account:
    """Return the account: which provider keys this request carried, AI limits, and recent
    usage."""
    try:
        return await _account(session, user)
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


@router.get(
    "/nightly-refresh",
    response_model=NightlyRefreshStatus,
    summary="Your nightly screener refresh",
)
async def read_nightly_refresh(
    user: CurrentUser,
    session: Session,
    use_key: Annotated[
        list[NightlyProvider],
        Query(description="Keys being considered, to preview what a refresh would use."),
    ] = [],  # noqa: B006
    price_history_stocks: Annotated[
        int | None,
        Query(ge=0, le=10_000, description="A price-history cap being considered, likewise."),
    ] = None,
    universe: Annotated[
        list[NightlyUniverse],
        Query(
            description="The sets a refresh would rebuild, likewise; every US stock and ETF "
            "when none is given."
        ),
    ] = [],  # noqa: B006
    custom_symbol: Annotated[
        list[str],
        Query(max_length=MAX_CUSTOM_SYMBOLS, description="Tickers it would add, likewise."),
    ] = [],  # noqa: B006
) -> NightlyRefreshStatus:
    """Return whether your nightly screener refresh is on, when it runs, which of your
    keys it stores, how its latest runs went, and about how many requests one refresh
    sends on each key (``key_use``). With ``use_key``, ``key_use`` previews a choice of
    keys, of ``price_history_stocks``, and of the sets (``universe``) and tickers
    (``custom_symbol``) it rebuilds, before they are saved.

    Returns 503 when the database is unavailable.
    """
    choice = UniverseChoice(
        universes=(
            tuple(dict.fromkeys(universe)) if universe or custom_symbol else tuple(EVERYTHING)
        ),
        custom_symbols=tuple(dict.fromkeys(symbol.strip().upper() for symbol in custom_symbol)),
    )
    preview = (list(use_key), price_history_stocks, choice) if use_key else None
    try:
        return await refresh_status(session, user.id, encryption_available(), preview=preview)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@router.put(
    "/nightly-refresh",
    response_model=NightlyRefreshStatus,
    summary="Turn on your nightly screener refresh",
)
async def store_nightly_refresh(
    user: CurrentUser, session: Session, settings: Annotated[NightlyRefreshSettings, Body()]
) -> NightlyRefreshStatus:
    """Opt in to a nightly refresh of your own screener, or change when it runs.

    Each night at ``refresh_time`` in ``time_zone``, your own universe (every US stock and
    ETF, or the sets ``universes`` chooses, combined, with the tickers in
    ``custom_symbols``) is rebuilt from the SEC's shared list of stocks and financials,
    Finnhub's free metrics on your Finnhub key, and price-history columns from whichever
    price-history key you have (Tiingo, FMP, EODHD, or Alpha Vantage). It runs with no
    browser open, so the market data keys this request carries are stored, encrypted,
    replacing any stored before; your Anthropic key is never stored. Send this again after
    changing a key.

    Returns 422 without a Finnhub key, or for an unknown time zone; 503 when this server
    cannot store keys or the database is unavailable.
    """
    try:
        await enable(session, user.id, settings, current_provider_keys())
        return await refresh_status(session, user.id, encryption_available())
    except MissingFinnhubKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except CredentialsUnavailableError as exc:
        logger.error("Cannot store keys for a nightly refresh: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This server cannot store keys, so the nightly refresh is not available.",
        ) from None
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@router.delete(
    "/nightly-refresh",
    response_model=NightlyRefreshStatus,
    summary="Turn off your nightly screener refresh",
)
async def remove_nightly_refresh(user: CurrentUser, session: Session) -> NightlyRefreshStatus:
    """Opt out of the nightly refresh: your stored keys are deleted at once, and no more
    refreshes run. Your last universe stays, with its date, until you delete your account.

    Returns 503 when the database is unavailable.
    """
    try:
        await disable(session, user.id)
        return await refresh_status(session, user.id, encryption_available())
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete your account",
)
async def delete_account(user: CurrentUser, session: Session) -> Response:
    """Delete your account and everything it holds, at once and for good.

    Your PyQxel API keys, sign-in links to GitHub and Google, holdings, stored backtests,
    analyses, and simulations, and your nightly screener refresh with the keys it stored
    and the universe it built are deleted, and every API key stops
    working. Signing in again afterwards starts a new, empty account. A usage subscription
    ends, billing the refreshes so far, and credit left is refunded to the cards that
    paid for it.
    """
    try:
        await _close_billing(session, user.id)
        await delete_user(session, user.id)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    logger.info("Deleted account %s at its owner's request.", user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _close_billing(session: AsyncSession, user_id: str) -> None:
    """End the user's subscription and refund their credit left, before the account
    that records them is deleted. Stripe's failures are logged, not raised: the account
    is deleted either way, and a subscription with no usage reported bills nothing."""
    if not stripe_api.billing_enabled():
        return
    account = await billing.get_account(session, user_id)
    if account is None:
        return
    try:
        await stripe_api.cancel_subscription(account)
        refunded = await stripe_api.refund_unused_credit(session, account)
    except stripe.StripeError as exc:
        logger.warning("Billing for account %s was not closed: %s", user_id, exc)
        return
    if refunded:
        logger.info("Refunded $%.2f of unused credit to account %s.", refunded / 100, user_id)


def _as_if_missing(request: Request) -> HTTPException:
    """The error the app gives ``request`` when the admin route is not there.

    Routing tries the other routes as Starlette would: a full match is the web frontend's
    catch-all, which answers API paths with 404; a path match with the wrong method is
    405, naming that route's methods; anything else is 404.
    """
    for route in request.app.router.routes:
        if getattr(route, "endpoint", None) is store_admin_settings:
            continue
        match, _ = route.matches(request.scope)
        if match is Match.FULL:
            break
        if match is Match.PARTIAL:
            methods = getattr(route, "methods", None) or ()
            return HTTPException(
                status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
                detail="Method Not Allowed",
                headers={"Allow": ", ".join(methods)},
            )
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


async def store_admin_settings(request: Request, session: Session) -> Account:
    """Turn the yfinance fallback for the admin's own requests on or off, with ``PUT``
    (``yfinance_fallback``), which never reaches another account, and the whole server's
    use of data sources not cleared for commercial use (``restricted_sources``), or the
    admin's own use of them while the server does not (``own_restricted_sources``), and
    whether the whole server offers the size, value, and momentum factors estimated from
    ETFs (``style_factors``); an omitted field is left as it is.

    Only the account ``ADMIN_EMAIL`` names may. Everyone else, signed in or not, gets the
    answer a path that does not exist gets, and the route is left out of the API schema,
    so no one can tell there are admin settings, or whose they are.
    """
    try:
        user = await authenticate(request)
    except HTTPException:
        raise _as_if_missing(request) from None
    if request.method != "PUT" or not user.is_admin:
        raise _as_if_missing(request)
    try:
        changes = AdminSettingsUpdate.model_validate_json(await request.body())
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    try:
        if changes.yfinance_fallback is not None:
            user = await update_yfinance_fallback(session, user.id, changes.yfinance_fallback)
        if changes.restricted_sources is not None:
            await set_setting(session, RESTRICTED_SOURCES, changes.restricted_sources)
        if changes.style_factors is not None:
            await set_setting(session, STYLE_FACTORS, changes.style_factors)
        if changes.own_restricted_sources is not None:
            user = await update_own_restricted_sources(
                session, user.id, changes.own_restricted_sources
            )
        logger.info("Admin %s changed admin settings: %s.", user.id, changes.model_dump())
        return await _account(session, user)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


def add_admin_route(application: FastAPI, prefix: str) -> None:
    """Add :func:`store_admin_settings` at ``{prefix}/admin``, outside the API router.

    The API router checks API keys before any of its routes run, answering 401; this
    route checks them itself instead, so it can answer like a path that does not exist.
    It is added as a plain route, which :func:`_as_if_missing` can tell from the others.
    """
    application.add_api_route(
        f"{prefix}/admin",
        store_admin_settings,
        methods=ALL_METHODS,
        response_model=Account,
        include_in_schema=False,
    )
