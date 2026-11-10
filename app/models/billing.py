"""Schemas for paying for the nightly refresh: prepaid credit and usage subscriptions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

BillingInterval = Literal["week", "month", "year"]
"""How often a usage subscription bills what the refreshes cost since the last bill."""

EntryKind = Literal["top_up", "usage", "fee", "refund", "dispute"]

MIN_TOP_UP_CENTS: int = 500
"""The smallest top-up: at $5, Stripe's fee of 30 cents plus 2.9% takes about 9%."""
MAX_TOP_UP_CENTS: int = 50_000
"""The largest top-up, $500: years of a full refresh, and a limit on what a stolen card
could put in."""
DEFAULT_TOP_UP_CENTS: int = 1_000

CARD_FEE_CENTS: int = 30
"""Stripe's fixed fee on each US card payment."""
CARD_FEE_RATE: float = 0.029
"""Stripe's share of each US card payment."""
BILLING_FEE_RATE: float = 0.007
"""Stripe Billing's share of each subscription bill, on top of the card fee."""
MIN_CHARGE_CENTS: int = 50
"""The least Stripe charges a card; a smaller bill waits for the next one."""


class BillingEntry(BaseModel):
    """One change to the user's billing."""

    kind: EntryKind = Field(
        description="top_up: credit bought, less Stripe's fee; usage: one nightly refresh's "
        "cost; fee: Stripe's fees on a subscription bill, charged like usage; refund or "
        "dispute: a top-up's money returned, taken back out of the credit."
    )
    credit_usd: float = Field(description="The change to the credit, in dollars.")
    subscription_usd: float = Field(
        description="A refresh's cost that the subscription bills, in dollars, since credit "
        "did not cover it."
    )
    created_at: datetime = Field(description="When it happened.")


class Subscription(BaseModel):
    """The user's usage subscription."""

    interval: BillingInterval = Field(description="How often Stripe bills the usage.")
    status: str = Field(
        description="Stripe's status: active, past_due (a payment failed and is being "
        "retried), unpaid, canceled, ..."
    )
    renews_at: datetime | None = Field(description="When the current period ends and is billed.")
    cancels: bool = Field(description="Whether it ends at the end of the current period.")


class BillingStatus(BaseModel):
    """What the user pays for their nightly refresh, and how."""

    enabled: bool = Field(
        description="Whether this server bills for the nightly refresh; without billing it "
        "is free."
    )
    exempt: bool = Field(description="Whether this account's refreshes are never billed.")
    credit_usd: float = Field(
        description="Prepaid credit left, in dollars; below zero when refreshes ran past it "
        "with no subscription, which the next top-up repays."
    )
    subscription: Subscription | None = Field(description="The usage subscription, if any.")
    can_refresh: bool = Field(
        description="Whether the nightly refresh may run: the account is exempt, or has "
        "credit left or a subscription Stripe is billing."
    )
    last_30_days_usd: float = Field(description="What refreshes cost in the last 30 days.")
    last_30_days_runs: int = Field(description="Refreshes in the last 30 days.")
    entries: list[BillingEntry] = Field(description="The latest changes, newest first.")
    min_top_up_cents: int = Field(description="The smallest top-up, in cents.")
    max_top_up_cents: int = Field(description="The largest top-up, in cents.")
    card_fee_cents: int = Field(description="Stripe's fixed fee on each card payment, in cents.")
    card_fee_rate: float = Field(description="Stripe's share of each card payment (0.029 = 2.9%).")
    billing_fee_rate: float = Field(
        description="Stripe Billing's share of each subscription bill (0.007 = 0.7%), on top "
        "of the card fee."
    )
    min_charge_cents: int = Field(
        description="The least Stripe charges a card, in cents; a smaller bill carries over."
    )


class TopUpRequest(BaseModel):
    """Credit to buy."""

    amount_cents: int = Field(
        ge=MIN_TOP_UP_CENTS,
        le=MAX_TOP_UP_CENTS,
        description="How much credit, in cents; the payment adds Stripe's fee to it.",
    )


class SubscriptionRequest(BaseModel):
    """A usage subscription to start."""

    interval: BillingInterval = Field(description="How often Stripe bills the usage.")


class CheckoutLink(BaseModel):
    """Where to send the browser to pay or manage billing on Stripe's own pages."""

    url: str = Field(description="A Stripe page; it returns to Settings when done.")
