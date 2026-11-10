"""Each user's prepaid credit, usage subscription, and the history of both.

PyQxel charges users what their nightly refreshes cost it to compute, at cost. A
refresh's cost is paid from the user's prepaid credit first; what credit does not cover is
billed through their usage subscription, if they have one, by reporting it to Stripe's
meter (:func:`app.billing.stripe_api.report_usage`); and with neither, the credit goes
below zero by that much, which the next top-up repays. A user with no credit left and no
subscription gets no refresh until they add one (:func:`can_refresh`).

Amounts are kept in millionths of a dollar ("micros"), since one night's refresh can cost
a fraction of a cent.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.tables import BillingAccountRecord, BillingEntryRecord, UserRecord

MICROS_PER_DOLLAR: int = 1_000_000
MICROS_PER_CENT: int = 10_000

TOP_UP = "top_up"
USAGE = "usage"
FEE = "fee"
REFUND = "refund"
DISPUTE = "dispute"

PAYING_STATUSES: frozenset[str] = frozenset({"active", "trialing"})
"""Subscription statuses whose usage Stripe will bill, so refreshes may run on them."""


@dataclass(frozen=True)
class Charge:
    """How one refresh's cost was paid."""

    from_credit: int
    """Micros taken from prepaid credit (or run up past it, with no subscription)."""
    to_subscription: int
    """Micros billed through the subscription."""


def has_subscription(account: BillingAccountRecord | None) -> bool:
    """Whether ``account`` has a subscription Stripe is billing."""
    return (
        account is not None
        and account.subscription_id is not None
        and account.subscription_status in PAYING_STATUSES
    )


def can_refresh(account: BillingAccountRecord | None) -> bool:
    """Whether a nightly refresh may run for ``account``: it has credit left, or a
    subscription that bills usage."""
    return account is not None and (account.credit_micros > 0 or has_subscription(account))


async def refresh_paid_for(session: AsyncSession, user_id: str) -> bool:
    """Whether ``user_id``'s nightly refresh may run: on a server that does not bill, for
    the owner's account (``ADMIN_EMAIL``), which pays the cloud bill itself, and for a user
    with credit left or a subscription Stripe is billing."""
    settings = get_settings()
    if settings.stripe_secret_key is None and not settings.nightly_billed:
        return True
    user = await session.get(UserRecord, user_id)
    if user is not None and settings.admin_email is not None and user.email == settings.admin_email:
        return True
    return can_refresh(await get_account(session, user_id))


async def get_account(session: AsyncSession, user_id: str) -> BillingAccountRecord | None:
    """``user_id``'s billing account, or ``None`` before they first pay."""
    return await session.get(BillingAccountRecord, user_id)


async def account_for_customer(
    session: AsyncSession, customer_id: str
) -> BillingAccountRecord | None:
    """The billing account of Stripe customer ``customer_id``, if it is one of PyQxel's."""
    return await session.scalar(
        select(BillingAccountRecord).where(BillingAccountRecord.stripe_customer_id == customer_id)
    )


async def create_account(
    session: AsyncSession, user_id: str, customer_id: str
) -> BillingAccountRecord:
    """Record ``customer_id`` as ``user_id``'s Stripe customer, with no credit yet."""
    now = datetime.now(timezone.utc)
    account = BillingAccountRecord(
        user_id=user_id,
        stripe_customer_id=customer_id,
        credit_micros=0,
        subscription_cancels=False,
        created_at=now,
        updated_at=now,
    )
    session.add(account)
    await session.commit()
    return account


async def _add_entry(session: AsyncSession, entry: BillingEntryRecord) -> bool:
    """Save ``entry`` with the account change already made in ``session``; return
    ``False``, changing nothing, if an entry from the same Stripe object exists."""
    session.add(entry)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return False
    return True


def _entry(user_id: str, kind: str, **fields: object) -> BillingEntryRecord:
    """A new entry for ``user_id``, made now."""
    return BillingEntryRecord(
        id=str(uuid4()),
        user_id=user_id,
        kind=kind,
        created_at=datetime.now(timezone.utc),
        **{"credit_micros": 0, "subscription_micros": 0, **fields},
    )


async def add_credit(
    session: AsyncSession,
    account: BillingAccountRecord,
    micros: int,
    *,
    stripe_ref: str,
    payment_intent: str | None,
) -> bool:
    """Add a paid top-up of ``micros`` to ``account``'s credit, once per ``stripe_ref``
    (the Checkout Session); return whether it was added now."""
    account.credit_micros += micros
    account.updated_at = datetime.now(timezone.utc)
    return await _add_entry(
        session,
        _entry(
            account.user_id,
            TOP_UP,
            credit_micros=micros,
            stripe_ref=stripe_ref,
            payment_intent=payment_intent,
        ),
    )


async def take_back_credit(
    session: AsyncSession,
    payment_intent: str,
    micros: int,
    *,
    kind: str,
    stripe_ref: str,
) -> bool:
    """Take ``micros`` of a refunded or disputed top-up back out of its user's credit,
    once per ``stripe_ref``, but never more than the top-up added, since Stripe's fee was
    never credited; return whether it was taken now. A payment that was not a top-up (a
    subscription's invoice) changes no credit, so nothing is taken."""
    top_up = await session.scalar(
        select(BillingEntryRecord).where(
            BillingEntryRecord.payment_intent == payment_intent,
            BillingEntryRecord.kind == TOP_UP,
        )
    )
    if top_up is None:
        return False
    account = await get_account(session, top_up.user_id)
    if account is None:
        return False
    earlier = await session.scalars(
        select(BillingEntryRecord).where(
            BillingEntryRecord.payment_intent == payment_intent,
            BillingEntryRecord.kind.in_((REFUND, DISPUTE)),
        )
    )
    left = top_up.credit_micros + sum(entry.credit_micros for entry in earlier)
    micros = min(micros, left)
    if micros <= 0:
        return False
    account.credit_micros -= micros
    account.updated_at = datetime.now(timezone.utc)
    return await _add_entry(
        session,
        _entry(
            account.user_id,
            kind,
            credit_micros=-micros,
            stripe_ref=stripe_ref,
            payment_intent=payment_intent,
        ),
    )


async def set_subscription(
    session: AsyncSession,
    account: BillingAccountRecord,
    *,
    subscription_id: str | None,
    status: str | None,
    interval: str | None,
    renews_at: datetime | None,
    cancels: bool,
) -> None:
    """Record the state Stripe reports for ``account``'s subscription; all ``None`` once
    it has ended."""
    account.subscription_id = subscription_id
    account.subscription_status = status
    account.subscription_interval = interval
    account.subscription_renews_at = renews_at
    account.subscription_cancels = cancels
    account.updated_at = datetime.now(timezone.utc)
    await session.commit()


async def charge_usage(
    session: AsyncSession,
    user_id: str,
    micros: int,
    run_id: str | None,
    *,
    kind: str = USAGE,
    stripe_ref: str | None = None,
) -> Charge | None:
    """Charge ``user_id`` ``micros`` for one refresh, or for the fees on a subscription
    bill (``kind`` :data:`FEE`, once per ``stripe_ref``): from credit first, the rest
    through their subscription, or past their credit with none. Returns ``None``,
    charging nothing, when the user has no billing account, as the owner's and every
    user's on a server without billing do not, or when it was charged already."""
    account = await get_account(session, user_id)
    if account is None or micros <= 0:
        return None
    from_credit = min(max(account.credit_micros, 0), micros)
    rest = micros - from_credit
    if has_subscription(account):
        to_subscription = rest
    else:
        from_credit, to_subscription = micros, 0
    account.credit_micros -= from_credit
    account.updated_at = datetime.now(timezone.utc)
    added = await _add_entry(
        session,
        _entry(
            user_id,
            kind,
            credit_micros=-from_credit,
            subscription_micros=to_subscription,
            run_id=run_id,
            stripe_ref=stripe_ref,
        ),
    )
    return Charge(from_credit=from_credit, to_subscription=to_subscription) if added else None


async def unreported_usage(session: AsyncSession) -> list[tuple[BillingEntryRecord, str]]:
    """Usage and fees billed through subscriptions and not yet reported to Stripe, with
    each one's Stripe customer."""
    rows = await session.execute(
        select(BillingEntryRecord, BillingAccountRecord.stripe_customer_id)
        .join(BillingAccountRecord, BillingAccountRecord.user_id == BillingEntryRecord.user_id)
        .where(
            BillingEntryRecord.kind.in_((USAGE, FEE)),
            BillingEntryRecord.subscription_micros > 0,
            BillingEntryRecord.reported_at.is_(None),
        )
        .order_by(BillingEntryRecord.created_at)
    )
    return [(entry, customer) for entry, customer in rows]


async def mark_reported(session: AsyncSession, entry_id: str, when: datetime) -> None:
    """Record that entry ``entry_id``'s usage reached Stripe's meter at ``when``."""
    entry = await session.get(BillingEntryRecord, entry_id)
    if entry is not None:
        entry.reported_at = when
        await session.commit()


async def recent_entries(
    session: AsyncSession, user_id: str, limit: int = 30
) -> list[BillingEntryRecord]:
    """``user_id``'s latest billing entries, newest first."""
    return list(
        await session.scalars(
            select(BillingEntryRecord)
            .where(BillingEntryRecord.user_id == user_id)
            .order_by(BillingEntryRecord.created_at.desc())
            .limit(limit)
        )
    )


async def usage_since(session: AsyncSession, user_id: str, since: datetime) -> tuple[int, int]:
    """The micros ``user_id``'s refreshes cost since ``since``, and how many there were."""
    entries = list(
        await session.scalars(
            select(BillingEntryRecord).where(
                BillingEntryRecord.user_id == user_id,
                BillingEntryRecord.kind == USAGE,
                BillingEntryRecord.created_at >= since,
            )
        )
    )
    return sum(-e.credit_micros + e.subscription_micros for e in entries), len(entries)
