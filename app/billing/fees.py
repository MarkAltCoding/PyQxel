"""Stripe's fees, which each user pays on their own payments, so no user costs PyQxel's owner
anything.

A top-up is charged its credit plus the fee Stripe will keep on a US card
(:func:`checkout_cents`), and the credit added is what is left once that fee is taken
(:func:`credit_micros`). Stripe's checkout shows the price before the card is entered, so a
card issued abroad has Stripe's extra 1.5% taken from its credit once it has paid
(:func:`international_micros`), and only such a card does. A usage subscription's bill
carries its own fees as a line Stripe adds while the bill is still a draft
(:func:`invoice_fee_cents`): the card fee, Stripe Billing's share, and the extra on a card
from abroad, all on the whole bill, fee line included. A dispute's $15 fee is charged to
the account that disputed.

The fees are Stripe's standard US prices, for cards and the wallets priced like them (Apple
Pay, Google Pay, Link); Checkout leaves out the payment methods that cost more
(:data:`EXCLUDED_METHODS`) and charges in dollars only, so no currency is converted.
"""

import math

from app.models.billing import (
    BILLING_FEE_RATE,
    CARD_FEE_CENTS,
    CARD_FEE_RATE,
    INTERNATIONAL_FEE_RATE,
    MIN_CHARGE_CENTS,
    TAX_FEE_RATE,
)

MICROS_PER_CENT: int = 10_000

EXCLUDED_METHODS: list[str] = ["klarna", "affirm", "afterpay_clearpay", "us_bank_account"]
"""Payment methods Checkout does not offer, since Stripe prices them differently from a
card: buying credit later in installments costs about twice as much, and a bank payment
less, so the fee charged would not match the fee paid."""


def card_fee_micros(cents: int) -> int:
    """Stripe's fee on a card payment of ``cents``, in millionths of a dollar."""
    return round((CARD_FEE_CENTS + cents * CARD_FEE_RATE) * MICROS_PER_CENT)


def checkout_cents(credit_cents: int) -> int:
    """What to charge for ``credit_cents`` of credit, so that the credit is left once
    Stripe keeps its fee: the smallest charge whose fee leaves at least that much."""
    return math.ceil((credit_cents + CARD_FEE_CENTS) / (1 - CARD_FEE_RATE))


def credit_micros(paid_cents: int) -> int:
    """The credit a payment of ``paid_cents`` buys: what is left after Stripe's fee."""
    return paid_cents * MICROS_PER_CENT - card_fee_micros(paid_cents)


def international_micros(paid_cents: int) -> int:
    """Stripe's extra fee on a payment of ``paid_cents`` by a card issued abroad."""
    return round(paid_cents * INTERNATIONAL_FEE_RATE * MICROS_PER_CENT)


def tax_fee_micros(paid_cents: int) -> int:
    """Stripe Tax's fee on a payment of ``paid_cents`` made where PyQxel collects tax."""
    return round(paid_cents * TAX_FEE_RATE * MICROS_PER_CENT)


def is_domestic(country: str | None) -> bool:
    """Whether a card issued in ``country`` pays only the US fee. A card whose country is
    unknown pays the extra too, so no payment costs PyQxel's owner anything."""
    return country == "US"


def invoice_fee_cents(
    due_cents: int, domestic: bool, tax_rate: float = 0.0, taxed: bool = False
) -> int:
    """The fee line to add to a subscription bill of ``due_cents`` so that the user pays
    every fee on it: the smallest amount at least Stripe's fees on the bill with that line
    included. Nothing for a bill too small for Stripe to charge, which waits for the next.

    ``tax_rate`` is the share of tax the bill already carries, which the fee line is taxed
    at too, so the bill grows by the line and its tax. ``taxed`` says the bill is made where
    PyQxel collects tax, so Stripe Tax's share is a fee on it as well."""
    if due_cents < MIN_CHARGE_CENTS:
        return 0
    rate = CARD_FEE_RATE + BILLING_FEE_RATE + (0 if domestic else INTERNATIONAL_FEE_RATE)
    rate += TAX_FEE_RATE if taxed else 0
    return math.ceil((CARD_FEE_CENTS + due_cents * rate) / (1 - rate * (1 + tax_rate)))
