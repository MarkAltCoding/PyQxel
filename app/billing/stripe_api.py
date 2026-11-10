"""Everything PyQxel asks of Stripe: its catalog, Checkout and the customer portal, usage
reports, and the events Stripe sends back.

PyQxel bills at cost. A user pays for their nightly refreshes with prepaid credit, bought
on a Stripe Checkout page in any amount from $5 to $500, or a usage subscription that
Stripe bills weekly, monthly, or yearly for what the refreshes cost in that period. The
ledger of credit and usage is PyQxel's own (:mod:`app.db.billing`); Stripe takes the
payments and, for subscriptions, adds up the usage PyQxel reports to its meter
(:data:`METER_EVENT`), billed at one ten-thousandth of a cent per micro, so a dollar of
refreshes is a dollar billed.

The catalog (two products, the meter, a price per interval, and the customer portal's
settings) is made once per Stripe account by ``python -m app.jobs.billing setup``, and
found again here by fixed IDs and lookup keys.

Webhook events are applied by :func:`handle_event`, idempotently, since Stripe may send
one more than once: credit for a paid top-up, the subscription's state, a refunded or
disputed top-up taken back out of the credit, and an early fraud warning on a top-up
refunded before it can become a dispute.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from functools import cache
from typing import Any

import httpx2
import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db import billing
from app.db.tables import BillingAccountRecord, BillingEntryRecord
from app.models.billing import BillingInterval

logger = logging.getLogger(__name__)

CREDIT_PRODUCT: str = "pyqxel_credit"
"""The product a top-up buys, by its fixed ID."""
USAGE_PRODUCT: str = "pyqxel_refresh_usage"
"""The product a usage subscription bills, by its fixed ID."""
METER_EVENT: str = "pyqxel_refresh_usage"
"""The meter event each refresh's cost is reported as, in micros."""
PRICE_KEYS: dict[BillingInterval, str] = {
    "week": "pyqxel_usage_week",
    "month": "pyqxel_usage_month",
    "year": "pyqxel_usage_year",
}
"""Each interval's metered price, by lookup key."""
CENTS_PER_MICRO: Decimal = Decimal("0.0001")
"""What one reported micro (a millionth of a dollar) is billed, in cents."""
PORTAL_TAG: str = "pyqxel"
"""The metadata key that marks the customer portal configuration PyQxel made."""

TOP_UP_FLOW: str = "pyqxel-top-up-kqmvxtrw"
"""The label Stripe's Dashboard groups top-up Checkout Sessions under."""
SUBSCRIBE_FLOW: str = "pyqxel-subscribe-hzpdnclg"
"""The label Stripe's Dashboard groups subscription Checkout Sessions under."""

NOT_MANAGED: Any = {"enabled": False}
"""PyQxel sells as itself, not through Stripe's Managed Payments, in which Stripe is the
merchant of record and handles sales tax for a further fee: billing is at cost, and
Managed Payments, on by default for new accounts, would add to every payment."""

EVENTS: tuple[str, ...] = (
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
    "charge.refunded",
    "charge.dispute.created",
    "radar.early_fraud_warning.created",
)
"""The events the webhook endpoint subscribes to: the ones :func:`handle_event` acts on."""


class BillingNotConfiguredError(RuntimeError):
    """``STRIPE_SECRET_KEY`` is unset, so this server does not bill."""


class BillingCatalogError(RuntimeError):
    """The Stripe account lacks the catalog that ``python -m app.jobs.billing setup`` makes."""


def billing_enabled() -> bool:
    """Whether this server bills for the nightly refresh."""
    return get_settings().stripe_secret_key is not None


@cache
def client() -> stripe.StripeClient:
    """The Stripe client, on the same HTTP library as the rest of the app.

    Raises:
        BillingNotConfiguredError: If ``STRIPE_SECRET_KEY`` is unset.
    """
    key = get_settings().stripe_secret_key
    if key is None:
        raise BillingNotConfiguredError("STRIPE_SECRET_KEY is unset.")
    return stripe.StripeClient(
        key.get_secret_value(),
        http_client=stripe.HTTPXClient(_lib=httpx2),
        max_network_retries=2,
    )


# --- The catalog ---------------------------------------------------------------------


async def _product(product_id: str, name: str, description: str) -> None:
    """Make product ``product_id`` unless it exists."""
    try:
        await client().v1.products.retrieve_async(product_id)
    except stripe.InvalidRequestError:
        await client().v1.products.create_async(
            {"id": product_id, "name": name, "description": description}
        )


async def _meter() -> str:
    """The ID of the usage meter, made if it does not exist."""
    meters = await client().v1.billing.meters.list_async({"status": "active", "limit": 100})
    for meter in meters.data:
        if meter.event_name == METER_EVENT:
            return meter.id
    made = await client().v1.billing.meters.create_async(
        {
            "display_name": "PyQxel nightly refresh usage (millionths of a dollar)",
            "event_name": METER_EVENT,
            "default_aggregation": {"formula": "sum"},
            "customer_mapping": {"type": "by_id", "event_payload_key": "stripe_customer_id"},
            "value_settings": {"event_payload_key": "value"},
        }
    )
    return made.id


async def setup_catalog(public_url: str | None) -> dict[str, str]:
    """Make whatever of PyQxel's catalog the Stripe account lacks; return what it holds.

    Safe to run again: each piece is found by its fixed ID, event name, lookup key, or tag.
    """
    await _product(
        CREDIT_PRODUCT,
        "PyQxel credit",
        "Prepaid credit that pays for your nightly screener refreshes at cost.",
    )
    await _product(
        USAGE_PRODUCT,
        "PyQxel nightly refresh usage",
        "What your nightly screener refreshes cost PyQxel to compute, billed at cost.",
    )
    meter = await _meter()
    held = {"credit product": CREDIT_PRODUCT, "usage product": USAGE_PRODUCT, "meter": meter}
    existing = await client().v1.prices.list_async(
        {"lookup_keys": list(PRICE_KEYS.values()), "active": True, "limit": 10}
    )
    found = {price.lookup_key: price.id for price in existing.data}
    for interval, key in PRICE_KEYS.items():
        if key not in found:
            price = await client().v1.prices.create_async(
                {
                    "currency": "usd",
                    "product": USAGE_PRODUCT,
                    "lookup_key": key,
                    "unit_amount_decimal": CENTS_PER_MICRO,
                    "recurring": {"interval": interval, "usage_type": "metered", "meter": meter},
                    "nickname": f"Usage, billed each {interval}",
                }
            )
            found[key] = price.id
        held[f"{interval} price"] = found[key]
    held["portal"] = await _portal(public_url)
    return held


async def _portal(public_url: str | None) -> str:
    """The ID of the customer portal configuration PyQxel uses, made if missing."""
    configurations = await client().v1.billing_portal.configurations.list_async(
        {"active": True, "limit": 100}
    )
    for configuration in configurations.data:
        if _metadata(configuration).get(PORTAL_TAG):
            return configuration.id
    params: Any = {
        "name": "PyQxel",
        "metadata": {PORTAL_TAG: "1"},
        "features": {
            "invoice_history": {"enabled": True},
            "payment_method_update": {"enabled": True},
            # Canceling at the period's end lets Stripe bill the usage up to then.
            "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
        },
        "business_profile": {"headline": "Pay for your nightly screener refresh, at cost."},
    }
    if public_url:
        params["business_profile"]["privacy_policy_url"] = f"{public_url}/privacy"
        params["default_return_url"] = f"{public_url}/settings#billing"
    made = await client().v1.billing_portal.configurations.create_async(params)
    return made.id


def _metadata(resource: Any) -> dict[str, str]:
    """A Stripe resource's metadata as a plain dictionary."""
    metadata = getattr(resource, "metadata", None)
    return {} if metadata is None else dict(metadata.to_dict())


@cache
def _cached() -> dict[str, str]:
    """IDs found in the catalog, kept for the process's life."""
    return {}


async def _price(interval: BillingInterval) -> str:
    """The metered price of ``interval``.

    Raises:
        BillingCatalogError: If the catalog has not been made.
    """
    key = PRICE_KEYS[interval]
    if key not in _cached():
        prices = await client().v1.prices.list_async(
            {"lookup_keys": [key], "active": True, "limit": 1}
        )
        if not prices.data:
            raise BillingCatalogError(f"No price {key}: run python -m app.jobs.billing setup.")
        _cached()[key] = prices.data[0].id
    return _cached()[key]


async def _portal_configuration() -> str | None:
    """PyQxel's customer portal configuration, or ``None`` for the account's default."""
    if PORTAL_TAG not in _cached():
        configurations = await client().v1.billing_portal.configurations.list_async(
            {"active": True, "limit": 100}
        )
        for configuration in configurations.data:
            if _metadata(configuration).get(PORTAL_TAG):
                _cached()[PORTAL_TAG] = configuration.id
    return _cached().get(PORTAL_TAG)


# --- Customers and Checkout ----------------------------------------------------------


async def account_of(session: AsyncSession, user_id: str, email: str) -> BillingAccountRecord:
    """``user_id``'s billing account, made with a new Stripe customer the first time."""
    account = await billing.get_account(session, user_id)
    if account is not None:
        return account
    customer = await client().v1.customers.create_async(
        {"email": email, "metadata": {"pyqxel_user_id": user_id}},
        # One customer per user, however many first payments are started at once.
        {"idempotency_key": f"pyqxel-customer-{user_id}"},
    )
    return await billing.create_account(session, user_id, customer.id)


async def top_up_link(account: BillingAccountRecord, cents: int, return_url: str) -> str:
    """A Checkout page that buys ``cents`` of credit."""
    checkout = await client().v1.checkout.sessions.create_async(
        {
            "mode": "payment",
            "customer": account.stripe_customer_id,
            "line_items": [
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": "usd",
                        "product": CREDIT_PRODUCT,
                        "unit_amount": cents,
                    },
                }
            ],
            "success_url": f"{return_url}?billing=paid#billing",
            "cancel_url": f"{return_url}#billing",
            "integration_identifier": TOP_UP_FLOW,
            "managed_payments": NOT_MANAGED,
        }
    )
    return _url(checkout.url)


async def subscribe_link(
    account: BillingAccountRecord, interval: BillingInterval, return_url: str
) -> str:
    """A Checkout page that starts a usage subscription billed each ``interval``."""
    checkout = await client().v1.checkout.sessions.create_async(
        {
            "mode": "subscription",
            "customer": account.stripe_customer_id,
            "line_items": [{"price": await _price(interval)}],
            "success_url": f"{return_url}?billing=subscribed#billing",
            "cancel_url": f"{return_url}#billing",
            "integration_identifier": SUBSCRIBE_FLOW,
            "managed_payments": NOT_MANAGED,
        }
    )
    return _url(checkout.url)


async def portal_link(account: BillingAccountRecord, return_url: str) -> str:
    """A customer portal page where the user changes their card, sees invoices, or
    cancels their subscription."""
    params: Any = {
        "customer": account.stripe_customer_id,
        "return_url": f"{return_url}#billing",
    }
    configuration = await _portal_configuration()
    if configuration is not None:
        params["configuration"] = configuration
    portal = await client().v1.billing_portal.sessions.create_async(params)
    return portal.url


def _url(url: str | None) -> str:
    """A Checkout Session's URL, which a hosted session always has."""
    if url is None:
        raise BillingCatalogError("Stripe returned a Checkout Session without a URL.")
    return url


async def cancel_subscription(account: BillingAccountRecord) -> None:
    """End ``account``'s subscription now, billing the usage so far, as when the account
    is deleted. A subscription Stripe already ended is left as it is."""
    if account.subscription_id is None:
        return
    try:
        await client().v1.subscriptions.cancel_async(account.subscription_id, {"invoice_now": True})
    except stripe.InvalidRequestError as exc:
        logger.info("Subscription %s was not canceled: %s", account.subscription_id, exc)


async def refund_unused_credit(session: AsyncSession, account: BillingAccountRecord) -> int:
    """Refund the credit ``account`` has left to the cards that paid for it, latest
    top-up first, as when the account is deleted; return the cents refunded. A whole cent
    or more is refunded; what is less, or a refund Stripe refuses, is logged and kept."""
    cents_left = account.credit_micros // billing.MICROS_PER_CENT
    refunded = 0
    top_ups = await session.scalars(
        select(BillingEntryRecord)
        .where(
            BillingEntryRecord.user_id == account.user_id,
            BillingEntryRecord.kind == billing.TOP_UP,
        )
        .order_by(BillingEntryRecord.created_at.desc())
    )
    for top_up in list(top_ups):
        if cents_left <= 0 or top_up.payment_intent is None:
            continue
        returned = await _taken_back(session, top_up.payment_intent, billing.REFUND)
        returned += await _taken_back(session, top_up.payment_intent, billing.DISPUTE)
        refundable = (top_up.credit_micros - returned) // billing.MICROS_PER_CENT
        amount = min(cents_left, refundable)
        if amount <= 0:
            continue
        try:
            await client().v1.refunds.create_async(
                {
                    "payment_intent": top_up.payment_intent,
                    "amount": amount,
                    "reason": "requested_by_customer",
                },
                {"idempotency_key": f"pyqxel-close-{top_up.id}-{amount}"},
            )
        except stripe.StripeError as exc:
            logger.warning("Top-up %s was not refunded: %s", top_up.payment_intent, exc)
            continue
        cents_left -= amount
        refunded += amount
    return refunded


# --- Usage reports -------------------------------------------------------------------


async def report_usage(session: AsyncSession) -> int:
    """Report to Stripe's meter the usage that subscriptions bill and Stripe has not been
    told of; return how many entries were reported.

    Each is sent with its entry's ID as the event's identifier, so Stripe counts an entry
    reported twice (if marking it failed) once. One that fails is left for the next try:
    the service calls this every hour.
    """
    reported = 0
    for entry, customer in await billing.unreported_usage(session):
        try:
            await client().v1.billing.meter_events.create_async(
                {
                    "event_name": METER_EVENT,
                    "identifier": entry.id,
                    "payload": {
                        "stripe_customer_id": customer,
                        "value": str(entry.subscription_micros),
                    },
                }
            )
        except stripe.InvalidRequestError as exc:
            if "identifier" not in str(exc).lower():
                logger.warning("Usage entry %s was not reported: %s", entry.id, exc)
                continue
            # Stripe already has this entry's event.
        except stripe.StripeError as exc:
            logger.warning("Stripe could not be told of usage entry %s: %s", entry.id, exc)
            break
        await billing.mark_reported(session, entry.id, datetime.now(timezone.utc))
        reported += 1
    return reported


# --- Webhook events ------------------------------------------------------------------


def verify_event(payload: bytes, signature: str) -> stripe.Event:
    """The event Stripe sent, once its signature proves Stripe sent it.

    Raises:
        BillingNotConfiguredError: If ``STRIPE_WEBHOOK_SECRET`` is unset.
        stripe.SignatureVerificationError: If the signature does not match.
        ValueError: If the payload is not an event.
    """
    secret = get_settings().stripe_webhook_secret
    if secret is None:
        raise BillingNotConfiguredError("STRIPE_WEBHOOK_SECRET is unset.")
    return client().construct_event(payload, signature, secret.get_secret_value())


def _id(value: Any) -> str | None:
    """The ID of an object Stripe gave as an ID or expanded."""
    if value is None or isinstance(value, str):
        return value
    return str(value.get("id")) if isinstance(value, dict) else None


def _time(value: Any) -> datetime | None:
    """A Stripe timestamp as a time."""
    return None if value is None else datetime.fromtimestamp(int(value), timezone.utc)


async def handle_event(session: AsyncSession, event: stripe.Event) -> None:
    """Apply one verified webhook event to PyQxel's records. Events PyQxel does not act on,
    or that name a customer it does not know, change nothing."""
    kind = event.type
    data = event.data.object.to_dict()
    if kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        await _checkout_paid(session, data)
    elif kind == "checkout.session.async_payment_failed":
        logger.info("Checkout Session %s: the payment failed.", data.get("id"))
    elif kind.startswith("customer.subscription."):
        await _subscription_changed(session, data, ended=kind.endswith(".deleted"))
    elif kind in ("invoice.paid", "invoice.payment_failed"):
        # The subscription's own update event carries the state this changes (past_due,
        # then active again), so the invoice is only logged.
        logger.info("Invoice %s for %s: %s.", data.get("id"), data.get("customer"), kind)
    elif kind == "charge.refunded":
        await _refunded(session, data, event.id)
    elif kind == "charge.dispute.created":
        await _disputed(session, data)
    elif kind == "radar.early_fraud_warning.created":
        await _fraud_warning(session, data)


async def _checkout_paid(session: AsyncSession, checkout: Any) -> None:
    """Add a paid top-up's credit. A subscription's Checkout needs nothing here: its
    ``customer.subscription.created`` event records it."""
    if checkout.get("mode") != "payment" or checkout.get("payment_status") == "unpaid":
        return
    customer = _id(checkout.get("customer"))
    account = None if customer is None else await billing.account_for_customer(session, customer)
    if account is None:
        logger.warning("Checkout Session %s names no PyQxel customer.", checkout.get("id"))
        return
    cents = int(checkout.get("amount_subtotal") or checkout.get("amount_total") or 0)
    added = await billing.add_credit(
        session,
        account,
        cents * billing.MICROS_PER_CENT,
        stripe_ref=str(checkout.get("id")),
        payment_intent=_id(checkout.get("payment_intent")),
    )
    if added:
        logger.info("User %s added $%.2f of credit.", account.user_id, cents / 100)


async def _subscription_changed(session: AsyncSession, subscription: Any, ended: bool) -> None:
    """Record a subscription's state, or that it ended."""
    customer = _id(subscription.get("customer"))
    account = None if customer is None else await billing.account_for_customer(session, customer)
    if account is None:
        return
    if account.subscription_id not in (None, subscription.get("id")) and ended:
        return  # An older subscription ended; the user has started another since.
    if ended or subscription.get("status") in ("canceled", "incomplete_expired"):
        await billing.set_subscription(
            session,
            account,
            subscription_id=None,
            status=None,
            interval=None,
            renews_at=None,
            cancels=False,
        )
        return
    items = (subscription.get("items") or {}).get("data") or []
    item = items[0] if items else {}
    recurring = (item.get("price") or {}).get("recurring") or {}
    await billing.set_subscription(
        session,
        account,
        subscription_id=subscription.get("id"),
        status=subscription.get("status"),
        interval=recurring.get("interval"),
        renews_at=_time(item.get("current_period_end") or subscription.get("current_period_end")),
        cancels=bool(subscription.get("cancel_at_period_end") or subscription.get("cancel_at")),
    )


async def _taken_back(session: AsyncSession, payment_intent: str, kind: str) -> int:
    """Micros already taken out of the credit for ``payment_intent``'s refunds or disputes."""
    entries = await session.scalars(
        select(BillingEntryRecord).where(
            BillingEntryRecord.payment_intent == payment_intent,
            BillingEntryRecord.kind == kind,
        )
    )
    return -sum(entry.credit_micros for entry in entries)


async def _refunded(session: AsyncSession, charge: Any, event_id: str) -> None:
    """Take a refunded top-up's money back out of the credit: what has been refunded in
    all, less what earlier refunds took."""
    payment_intent = _id(charge.get("payment_intent"))
    if payment_intent is None:
        return
    refunded = int(charge.get("amount_refunded") or 0) * billing.MICROS_PER_CENT
    owed = refunded - await _taken_back(session, payment_intent, billing.REFUND)
    if owed > 0:
        await billing.take_back_credit(
            session, payment_intent, owed, kind=billing.REFUND, stripe_ref=event_id
        )


async def _disputed(session: AsyncSession, dispute: Any) -> None:
    """Take a disputed top-up's money back out of the credit: the card's bank has
    already returned it to the cardholder."""
    payment_intent = _id(dispute.get("payment_intent"))
    if payment_intent is None:
        return
    taken = await billing.take_back_credit(
        session,
        payment_intent,
        int(dispute.get("amount") or 0) * billing.MICROS_PER_CENT,
        kind=billing.DISPUTE,
        stripe_ref=str(dispute.get("id")),
    )
    if taken:
        logger.warning("Top-up %s was disputed (%s).", payment_intent, dispute.get("reason"))


async def _fraud_warning(session: AsyncSession, warning: Any) -> None:
    """Refund a top-up that a card network warns was likely fraud, before it becomes a
    dispute and its fee; the refund's own event takes the credit back."""
    payment_intent = _id(warning.get("payment_intent"))
    if payment_intent is None or warning.get("actionable") is False:
        return
    top_up = await session.scalar(
        select(BillingEntryRecord).where(
            BillingEntryRecord.payment_intent == payment_intent,
            BillingEntryRecord.kind == billing.TOP_UP,
        )
    )
    if top_up is None:
        return
    try:
        await client().v1.refunds.create_async(
            {"payment_intent": payment_intent, "reason": "fraudulent"},
            {"idempotency_key": f"pyqxel-efw-{warning.get('id')}"},
        )
        logger.warning("Top-up %s was refunded on an early fraud warning.", payment_intent)
    except stripe.InvalidRequestError as exc:
        logger.info("Top-up %s was not refunded: %s", payment_intent, exc)
