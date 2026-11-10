"""Stripe's fees, which each user pays on their own payments, so no user costs PyQxel's owner
anything.

A top-up is charged its credit plus the fee Stripe will keep (:func:`checkout_cents`), and
the credit added is what is left once that fee is taken (:func:`credit_micros`). A usage
subscription's bill pays Stripe's fee and Stripe Billing's share of the amount
(:func:`invoice_fee_micros`); that is charged to the user afterwards, like a refresh: from
credit, else on the next bill.

The fees are Stripe's standard US prices, for cards and the wallets priced like them (Apple
Pay, Google Pay, Link); Checkout leaves out the payment methods that cost more
(:data:`EXCLUDED_METHODS`). Not passed on: the extra 1.5% Stripe charges on a card issued
abroad, and the $15 fee on a dispute.
"""

import math

from app.models.billing import BILLING_FEE_RATE, CARD_FEE_CENTS, CARD_FEE_RATE

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


def invoice_fee_micros(paid_cents: int) -> int:
    """What a subscription bill of ``paid_cents`` costs in fees: Stripe's card fee and
    Stripe Billing's share. Nothing for a bill that charged nothing, as one under Stripe's
    50-cent minimum does."""
    if paid_cents <= 0:
        return 0
    return card_fee_micros(paid_cents) + round(paid_cents * BILLING_FEE_RATE * MICROS_PER_CENT)
