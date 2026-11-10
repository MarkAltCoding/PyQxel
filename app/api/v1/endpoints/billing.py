"""Paying for the nightly refresh: prepaid credit, usage subscriptions, and their history.

PyQxel charges each user, at cost, what their nightly screener refreshes cost it to
compute; using the website and app is free. Payments happen on Stripe's own pages: these
routes return a link to one, and Stripe's webhook (:mod:`app.api.webhooks`) records the
result.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import CurrentUser
from app.billing import stripe_api
from app.core.config import get_settings
from app.db import billing
from app.db.session import get_session
from app.db.tables import BillingAccountRecord, BillingEntryRecord
from app.models.billing import (
    BILLING_FEE_RATE,
    CARD_FEE_CENTS,
    CARD_FEE_RATE,
    MAX_TOP_UP_CENTS,
    MIN_CHARGE_CENTS,
    MIN_TOP_UP_CENTS,
    BillingEntry,
    BillingStatus,
    CheckoutLink,
    Subscription,
    SubscriptionRequest,
    TopUpRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]

DEV_WEB_ORIGIN: str = "http://localhost:5173"


def _dollars(micros: int) -> float:
    """Micros as dollars."""
    return micros / billing.MICROS_PER_DOLLAR


def _entry(entry: BillingEntryRecord) -> BillingEntry:
    """A billing entry as the API shows it."""
    return BillingEntry.model_validate(
        {
            "kind": entry.kind,
            "credit_usd": _dollars(entry.credit_micros),
            "subscription_usd": _dollars(entry.subscription_micros),
            "created_at": entry.created_at,
        }
    )


def _subscription(account: BillingAccountRecord | None) -> Subscription | None:
    """``account``'s subscription as the API shows it."""
    if account is None or account.subscription_id is None or account.subscription_interval is None:
        return None
    return Subscription.model_validate(
        {
            "interval": account.subscription_interval,
            "status": account.subscription_status or "incomplete",
            "renews_at": account.subscription_renews_at,
            "cancels": account.subscription_cancels,
        }
    )


def _return_url(origin: str | None) -> str:
    """The Settings page Stripe's pages send the browser back to: the site that asked, if
    the server allows it, else the public one."""
    settings = get_settings()
    if origin and origin.startswith("http") and origin in settings.cors_origins:
        base = origin
    else:
        base = settings.public_url or DEV_WEB_ORIGIN
    return f"{base.rstrip('/')}/settings"


def _unavailable(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail)


def _require_billing() -> None:
    """Raise 404 on a server that does not bill."""
    if not stripe_api.billing_enabled():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This server does not bill for anything.")


@router.get("", response_model=BillingStatus, summary="Your billing")
async def read_billing(user: CurrentUser, session: Session) -> BillingStatus:
    """Your prepaid credit, usage subscription, what your nightly refreshes cost in the
    last 30 days, and your latest billing history.

    Returns 503 when the database is unavailable.
    """
    try:
        account = await billing.get_account(session, user.id)
        since = datetime.now(timezone.utc) - timedelta(days=30)
        spent, runs = await billing.usage_since(session, user.id, since)
        entries = await billing.recent_entries(session, user.id)
    except SQLAlchemyError as exc:
        raise _unavailable("The database is unavailable.") from exc
    enabled = stripe_api.billing_enabled()
    exempt = user.is_admin
    return BillingStatus(
        enabled=enabled,
        exempt=exempt,
        credit_usd=_dollars(account.credit_micros) if account else 0.0,
        subscription=_subscription(account),
        can_refresh=not enabled or exempt or billing.can_refresh(account),
        last_30_days_usd=_dollars(spent),
        last_30_days_runs=runs,
        entries=[_entry(entry) for entry in entries],
        min_top_up_cents=MIN_TOP_UP_CENTS,
        max_top_up_cents=MAX_TOP_UP_CENTS,
        card_fee_cents=CARD_FEE_CENTS,
        card_fee_rate=CARD_FEE_RATE,
        billing_fee_rate=BILLING_FEE_RATE,
        min_charge_cents=MIN_CHARGE_CENTS,
    )


async def _account(session: AsyncSession, user: CurrentUser) -> BillingAccountRecord:
    """The user's billing account, with a Stripe customer made the first time."""
    return await stripe_api.account_of(session, user.id, user.email)


@router.post("/top-up", response_model=CheckoutLink, summary="Buy credit")
async def buy_credit(
    user: CurrentUser,
    session: Session,
    request: TopUpRequest,
    origin: Annotated[str | None, Header()] = None,
) -> CheckoutLink:
    """A Stripe Checkout page that buys ``amount_cents`` of credit, from $5 to $500, charging
    Stripe's fee on top so the credit is the amount asked for. The credit is added when
    Stripe reports the payment, usually within seconds.

    Returns 404 when this server does not bill, 422 for an amount out of range, 502 when
    Stripe cannot be reached, and 503 when the database is unavailable.
    """
    _require_billing()
    try:
        account = await _account(session, user)
        url = await stripe_api.top_up_link(account, request.amount_cents, _return_url(origin))
    except SQLAlchemyError as exc:
        raise _unavailable("The database is unavailable.") from exc
    except (stripe.StripeError, stripe_api.BillingCatalogError) as exc:
        logger.warning("No top-up page for user %s: %s", user.id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Stripe could not be reached.") from exc
    return CheckoutLink(url=url)


@router.post("/subscription", response_model=CheckoutLink, summary="Start a usage subscription")
async def subscribe(
    user: CurrentUser,
    session: Session,
    request: SubscriptionRequest,
    origin: Annotated[str | None, Header()] = None,
) -> CheckoutLink:
    """A Stripe Checkout page that starts a subscription billing your refreshes' cost each
    week, month, or year, after any credit you have is used.

    Returns 404 when this server does not bill, 409 when you already have a subscription
    (change or cancel it in the billing portal), 502 when Stripe cannot be reached, and
    503 when the database is unavailable.
    """
    _require_billing()
    try:
        account = await _account(session, user)
        if billing.has_subscription(account) or account.subscription_status == "past_due":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "You already have a subscription; change or cancel it in the billing portal.",
            )
        url = await stripe_api.subscribe_link(account, request.interval, _return_url(origin))
    except SQLAlchemyError as exc:
        raise _unavailable("The database is unavailable.") from exc
    except (stripe.StripeError, stripe_api.BillingCatalogError) as exc:
        logger.warning("No subscription page for user %s: %s", user.id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Stripe could not be reached.") from exc
    return CheckoutLink(url=url)


@router.post("/portal", response_model=CheckoutLink, summary="Manage your billing")
async def manage_billing(
    user: CurrentUser,
    session: Session,
    origin: Annotated[str | None, Header()] = None,
) -> CheckoutLink:
    """A Stripe page where you change your card, see your invoices and receipts, or cancel
    your subscription.

    Returns 404 when this server does not bill or you have never paid, 502 when Stripe
    cannot be reached, and 503 when the database is unavailable.
    """
    _require_billing()
    try:
        account = await billing.get_account(session, user.id)
        if account is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "You have not paid for anything yet.")
        url = await stripe_api.portal_link(account, _return_url(origin))
    except SQLAlchemyError as exc:
        raise _unavailable("The database is unavailable.") from exc
    except stripe.StripeError as exc:
        logger.warning("No billing portal for user %s: %s", user.id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Stripe could not be reached.") from exc
    return CheckoutLink(url=url)
