"""How much of each plan the user's keys have used, what a page would use, and the price
history PyQxel keeps for the user."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import CurrentUser
from app.core.credentials import current_provider_keys
from app.data import estimates, usage
from app.data.fetcher import eodhd_account, forget_stored_prices
from app.db.price_store import delete_histories, store_summary
from app.db.session import get_session
from app.models.usage import (
    ProviderEstimate,
    ProviderUsage,
    StoredPrices,
    UsageEstimate,
    UsageEstimateRequest,
    UsageResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()
Session = Annotated[AsyncSession, Depends(get_session)]

KEY_ORDER: tuple[usage.UsageProvider, ...] = (
    "tiingo",
    "fmp",
    "eodhd",
    "finnhub",
    "alpha_vantage",
    "anthropic",
)
"""The keys in the order Settings lists them."""


def _plans(choices: list[str]) -> dict[str, str]:
    """``provider:plan`` choices as a mapping."""
    found: dict[str, str] = {}
    for choice in choices:
        provider, _, plan = choice.partition(":")
        if provider and plan:
            found[provider.strip()] = plan.strip()
    return found


def _keys() -> dict[usage.UsageProvider, str]:
    """The keys the request carried, by provider."""
    keys = current_provider_keys()
    found: dict[usage.UsageProvider, str] = {}
    for provider in KEY_ORDER:
        secret = getattr(keys, provider, None)
        if secret is not None:
            found[provider] = secret.get_secret_value()
    return found


async def _reports(
    keys: dict[usage.UsageProvider, str], plans: dict[str, str]
) -> list[ProviderUsage]:
    """Each key's report; EODHD's account is read for its own figures."""
    account = await eodhd_account(keys["eodhd"]) if "eodhd" in keys else None
    # A provider that answered that a key's limit is reached counted use from anywhere.
    reached = {
        provider: await estimates.paused_until(provider, key) for provider, key in keys.items()
    }
    return list(
        await asyncio.gather(
            *(
                usage.provider_report(
                    provider,
                    key,
                    plans.get(provider),
                    account if provider == "eodhd" else None,
                    reached[provider],
                )
                for provider, key in keys.items()
            )
        )
    )


@router.get("", response_model=UsageResponse, summary="Your keys' plan usage")
async def read_usage(
    user: CurrentUser,
    session: Session,
    plan: Annotated[
        list[str],
        Query(
            description="The plan each key is on, as ``provider:plan`` (e.g. "
            "``tiingo:power``); a key not named is taken to be on the free plan."
        ),
    ] = [],  # noqa: B006
) -> UsageResponse:
    """Return, for each key the request carries, its plan's limits (per second, minute,
    hour, day or month, and different symbols a month where one applies), how much of
    each is used and left, and when each resets.

    EODHD's daily figures come from its account page, and Finnhub's, EODHD's and
    Anthropic's per-minute figures from the headers of their latest answers, so they count
    use from anywhere. Tiingo, FMP and Alpha Vantage report nothing, so their figures are
    PyQxel's own count of its requests, labeled ``pyqxel``. Also says how much price
    history PyQxel keeps for the user. Returns 503 when the database is unavailable.
    """
    try:
        providers = await _reports(_keys(), _plans(plan))
        summary = await store_summary(session, user.id)
    except SQLAlchemyError as exc:
        logger.error("Usage could not be read: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Usage figures are unavailable while the database is down.",
        ) from exc
    return UsageResponse(
        providers=providers,
        stored_prices=StoredPrices(
            symbols=summary.symbols,
            histories=summary.histories,
            bytes=summary.bytes,
            oldest_fetch=summary.oldest_fetch,
        ),
    )


@router.post("/estimate", response_model=UsageEstimate, summary="What loading a page would use")
async def estimate_usage(
    user: CurrentUser, request: Annotated[UsageEstimateRequest, Body()]
) -> UsageEstimate:
    """Estimate how many requests fetching ``fetches`` would send on each key, cheapest
    key first as the fetches themselves try them, and weigh them against each plan's
    tightest limit. Histories the user's stored prices hold count as free. Nothing is
    fetched. Returns 503 when the database is unavailable.
    """
    try:
        requests, asked, from_store = await estimates.estimate(
            [(f.kind, f.symbols, f.period, f.interval) for f in request.fetches]
        )
        keys = _keys()
        counts: dict[str, int] = {str(provider): n for provider, n in requests.items()}
        symbols: dict[str, set[str]] = {str(provider): names for provider, names in asked.items()}
        wanted: dict[usage.UsageProvider, str] = {
            provider: keys[provider]
            for provider in KEY_ORDER
            if counts.get(provider) and provider in keys
        }
        reports = {r.provider: r for r in await _reports(wanted, request.plans)}
        found: list[ProviderEstimate] = []
        for provider, key in wanted.items():
            report = reports.get(provider)
            fresh = (
                await estimates.new_symbols(provider, key, symbols.get(provider, set()))
                if provider in usage.SYMBOL_LIMITED
                else None
            )
            found.append(
                ProviderEstimate(
                    provider=provider,
                    name=usage.PROVIDER_NAMES[provider],
                    requests=counts[provider],
                    new_symbols=fresh,
                    tightest=None if report is None else usage.tightest(report),
                )
            )
    except SQLAlchemyError as exc:
        logger.error("A usage estimate failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Usage estimates are unavailable while the database is down.",
        ) from exc
    return UsageEstimate(providers=found, from_store=from_store)


@router.delete(
    "/stored-prices",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete your stored prices",
)
async def remove_stored_prices(user: CurrentUser, session: Session) -> Response:
    """Delete every price history PyQxel keeps for you. Later pages fetch prices again on
    your keys. Returns 503 when the database is unavailable."""
    try:
        await delete_histories(session, user.id)
    except SQLAlchemyError as exc:
        logger.error("Stored prices could not be deleted: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stored prices cannot be deleted while the database is down.",
        ) from exc
    forget_stored_prices(user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
