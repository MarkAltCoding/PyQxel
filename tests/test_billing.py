"""Billing users at cost for their nightly refreshes: credit, subscriptions, Stripe's
events, usage reports, and which refreshes may run."""

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Coroutine
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.v1.endpoints import billing as billing_route
from app.billing import stripe_api
from app.core.config import get_settings
from app.db import billing
from app.db import nightly as nightly_storage
from app.db.session import get_sessionmaker
from app.db.tables import BillingAccountRecord, NightlyRefreshRecord
from app.db.users import User
from app.jobs import nightly as nightly_job
from app.main import app

client = TestClient(app)

WEBHOOK_SECRET = "whsec_test_secret"
CUSTOMER = "cus_test_1"
MICROS = billing.MICROS_PER_DOLLAR


@pytest.fixture
def billed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that bills, with a Stripe key no test sends anything on."""
    monkeypatch.setattr(get_settings(), "stripe_secret_key", SecretStr("sk_test_unused"))
    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", SecretStr(WEBHOOK_SECRET))
    stripe_api.client.cache_clear()


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


async def _account(user_id: str) -> BillingAccountRecord | None:
    async with get_sessionmaker()() as session:
        return await billing.get_account(session, user_id)


def _make_account(user: User, credit: int = 0, subscription: str | None = None) -> None:
    async def make() -> None:
        async with get_sessionmaker()() as session:
            account = await billing.create_account(session, user.id, CUSTOMER)
            account.credit_micros = credit
            if subscription:
                account.subscription_id = "sub_1"
                account.subscription_status = subscription
                account.subscription_interval = "month"
            await session.commit()

    _run(make())


def _charge(user: User, micros: int) -> billing.Charge | None:
    async def charge() -> billing.Charge | None:
        async with get_sessionmaker()() as session:
            return await billing.charge_usage(session, user.id, micros, "run-1")

    return _run(charge())


def _send(event_type: str, obj: dict[str, Any], event_id: str = "evt_1") -> int:
    """Post a webhook event signed as Stripe signs them; return the response's status."""
    payload = json.dumps(
        {
            "id": event_id,
            "object": "event",
            "type": event_type,
            "data": {"object": obj},
            "api_version": "2026-10-28.endive",
        }
    )
    stamp = int(time.time())
    signature = hmac.new(
        WEBHOOK_SECRET.encode(), f"{stamp}.{payload}".encode(), hashlib.sha256
    ).hexdigest()
    return client.post(
        "/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": f"t={stamp},v1={signature}"},
    ).status_code


# --- Paying for a refresh ------------------------------------------------------------


def test_usage_is_paid_from_credit_first_then_the_subscription(user: User) -> None:
    _make_account(user, credit=30_000, subscription="active")

    charge = _charge(user, 100_000)

    assert charge == billing.Charge(from_credit=30_000, to_subscription=70_000)
    account = _run(_account(user.id))
    assert account is not None and account.credit_micros == 0


def test_without_a_subscription_usage_runs_the_credit_below_zero(user: User) -> None:
    _make_account(user, credit=30_000)

    assert _charge(user, 100_000) == billing.Charge(from_credit=100_000, to_subscription=0)
    account = _run(_account(user.id))
    assert account is not None and account.credit_micros == -70_000


def test_a_user_who_never_paid_is_charged_nothing(user: User) -> None:
    assert _charge(user, 100_000) is None


def test_a_run_costs_its_share_of_the_jobs_time() -> None:
    # One vCPU and 1 GiB for an hour: 3,600 x ($0.000018 + $0.000002) = 7.2 cents, and a
    # third of that when three refreshes share the job.
    assert nightly_job.run_cost_micros(3600, 1) == 72_000
    assert nightly_job.run_cost_micros(3600, 3) == 24_000


# --- Which refreshes may run ---------------------------------------------------------


def _paid_for(user: User) -> bool:
    async def check() -> bool:
        async with get_sessionmaker()() as session:
            return await billing.refresh_paid_for(session, user.id)

    return _run(check())


def test_without_billing_every_refresh_runs(user: User) -> None:
    assert _paid_for(user)


@pytest.mark.usefixtures("billed")
def test_with_billing_a_refresh_needs_credit_or_a_subscription(user: User) -> None:
    assert not _paid_for(user)
    _make_account(user, credit=0)
    assert not _paid_for(user)

    async def set_credit(micros: int, status: str | None) -> None:
        async with get_sessionmaker()() as session:
            account = await billing.get_account(session, user.id)
            assert account is not None
            account.credit_micros = micros
            account.subscription_id = "sub_1" if status else None
            account.subscription_status = status
            await session.commit()

    _run(set_credit(1, None))
    assert _paid_for(user)
    _run(set_credit(-5, "active"))
    assert _paid_for(user)
    # A failed payment stops refreshes until it is paid.
    _run(set_credit(-5, "past_due"))
    assert not _paid_for(user)


def test_the_nightly_job_knows_refreshes_are_billed_without_a_stripe_key(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "nightly_billed", True)

    assert not _paid_for(user)
    _make_account(user, credit=1)
    assert _paid_for(user)


@pytest.mark.usefixtures("billed")
def test_the_owners_refresh_is_never_billed(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "admin_email", user.email)

    assert _paid_for(user)


@pytest.mark.usefixtures("billed")
def test_an_unpaid_refresh_is_not_due(user: User) -> None:
    async def due() -> tuple[bool, int]:
        now = datetime.now(timezone.utc)
        async with get_sessionmaker()() as session:
            session.add(
                NightlyRefreshRecord(
                    user_id=user.id,
                    refresh_time="00:00",
                    time_zone="UTC",
                    credentials="unread here",
                    providers=["finnhub"],
                    created_at=now,
                    keys_updated_at=now,
                )
            )
            await session.commit()
            return (
                await nightly_storage.any_due(session, now),
                len(await nightly_storage.due_refreshes(session, now)),
            )

    assert _run(due()) == (False, 0)


# --- Stripe's events -----------------------------------------------------------------


def _top_up_session(cents: int = 1000, session_id: str = "cs_1") -> dict[str, Any]:
    return {
        "id": session_id,
        "object": "checkout.session",
        "mode": "payment",
        "payment_status": "paid",
        "customer": CUSTOMER,
        "amount_subtotal": cents,
        "amount_total": cents,
        "payment_intent": "pi_1",
    }


@pytest.mark.usefixtures("billed")
def test_an_unsigned_event_is_refused(user: User) -> None:
    response = client.post(
        "/webhooks/stripe",
        content=json.dumps({"type": "checkout.session.completed"}),
        headers={"Stripe-Signature": "t=1,v1=forged"},
    )
    assert response.status_code == 400


def test_without_billing_the_webhook_is_not_found() -> None:
    assert client.post("/webhooks/stripe", content="{}").status_code == 404


@pytest.mark.usefixtures("billed")
def test_a_paid_top_up_adds_credit_once(user: User) -> None:
    _make_account(user)

    assert _send("checkout.session.completed", _top_up_session(1000)) == 204
    # Stripe sends an event again when unsure it arrived.
    assert _send("checkout.session.completed", _top_up_session(1000), "evt_2") == 204

    account = _run(_account(user.id))
    assert account is not None and account.credit_micros == 10 * MICROS


@pytest.mark.usefixtures("billed")
def test_a_top_up_still_unpaid_adds_nothing_until_it_is_paid(user: User) -> None:
    _make_account(user)
    unpaid = {**_top_up_session(), "payment_status": "unpaid"}

    _send("checkout.session.completed", unpaid)
    assert (account := _run(_account(user.id))) is not None and account.credit_micros == 0

    _send("checkout.session.async_payment_succeeded", _top_up_session(), "evt_2")
    assert (account := _run(_account(user.id))) is not None and account.credit_micros == 10 * MICROS


@pytest.mark.usefixtures("billed")
def test_refunds_and_disputes_take_a_top_up_back_out_of_the_credit(user: User) -> None:
    _make_account(user)
    _send("checkout.session.completed", _top_up_session(2000))

    # Two partial refunds: Stripe reports the total refunded on each.
    refund = {"id": "ch_1", "object": "charge", "payment_intent": "pi_1"}
    _send("charge.refunded", {**refund, "amount_refunded": 500}, "evt_r1")
    _send("charge.refunded", {**refund, "amount_refunded": 800}, "evt_r2")
    _send("charge.refunded", {**refund, "amount_refunded": 800}, "evt_r2")
    assert (account := _run(_account(user.id))) is not None and account.credit_micros == 12 * MICROS

    dispute = {"id": "dp_1", "object": "dispute", "payment_intent": "pi_1", "amount": 1200}
    _send("charge.dispute.created", dispute, "evt_d1")
    assert (account := _run(_account(user.id))) is not None and account.credit_micros == 0


@pytest.mark.usefixtures("billed")
def test_a_subscription_is_recorded_and_ended(user: User) -> None:
    _make_account(user)
    period_end = int(datetime(2026, 12, 10, tzinfo=timezone.utc).timestamp())
    subscription = {
        "id": "sub_9",
        "object": "subscription",
        "customer": CUSTOMER,
        "status": "active",
        "cancel_at_period_end": False,
        "items": {
            "object": "list",
            "data": [
                {
                    "id": "si_1",
                    "object": "subscription_item",
                    "current_period_end": period_end,
                    "price": {
                        "id": "price_w",
                        "object": "price",
                        "recurring": {"interval": "week"},
                    },
                }
            ],
        },
    }

    _send("customer.subscription.created", subscription)
    account = _run(_account(user.id))
    assert account is not None
    assert (account.subscription_id, account.subscription_status) == ("sub_9", "active")
    assert account.subscription_interval == "week"
    assert account.subscription_renews_at is not None

    _send("customer.subscription.deleted", {**subscription, "status": "canceled"}, "evt_2")
    account = _run(_account(user.id))
    assert account is not None and account.subscription_id is None


# --- Usage reports -------------------------------------------------------------------


@pytest.mark.usefixtures("billed")
def test_subscription_usage_is_reported_to_stripes_meter_once(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_account(user, credit=10_000, subscription="active")
    _charge(user, 50_000)
    sent: list[dict[str, Any]] = []

    async def create_async(params: dict[str, Any]) -> None:
        sent.append(params)

    fake = SimpleNamespace(
        v1=SimpleNamespace(
            billing=SimpleNamespace(meter_events=SimpleNamespace(create_async=create_async))
        )
    )
    monkeypatch.setattr(stripe_api, "client", lambda: fake)

    async def report() -> int:
        async with get_sessionmaker()() as session:
            return await stripe_api.report_usage(session)

    assert _run(report()) == 1
    assert _run(report()) == 0
    assert len(sent) == 1
    assert sent[0]["event_name"] == stripe_api.METER_EVENT
    assert sent[0]["payload"] == {"stripe_customer_id": CUSTOMER, "value": "40000"}


# --- The routes ----------------------------------------------------------------------


def test_billing_reads_as_off_without_stripe(user: User) -> None:
    status = client.get("/api/v1/me/billing").json()

    assert status["enabled"] is False and status["can_refresh"] is True
    assert client.post("/api/v1/me/billing/top-up", json={"amount_cents": 1000}).status_code == 404


@pytest.mark.usefixtures("billed")
def test_billing_shows_credit_usage_and_history(user: User) -> None:
    _make_account(user, credit=3 * MICROS)
    _charge(user, 250_000)

    status = client.get("/api/v1/me/billing").json()

    assert status["enabled"] is True and status["can_refresh"] is True
    assert status["credit_usd"] == pytest.approx(2.75)
    assert status["last_30_days_usd"] == pytest.approx(0.25)
    assert status["last_30_days_runs"] == 1
    assert status["entries"][0]["kind"] == "usage"


@pytest.mark.usefixtures("billed")
def test_buying_credit_returns_a_checkout_page(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[int, str]] = []

    async def account_of(session: Any, user_id: str, email: str) -> BillingAccountRecord:
        return await billing.create_account(session, user_id, CUSTOMER)

    async def top_up_link(account: BillingAccountRecord, cents: int, return_url: str) -> str:
        asked.append((cents, return_url))
        return "https://checkout.stripe.com/c/pay/cs_test"

    monkeypatch.setattr(stripe_api, "account_of", account_of)
    monkeypatch.setattr(stripe_api, "top_up_link", top_up_link)

    response = client.post(
        "/api/v1/me/billing/top-up",
        json={"amount_cents": 1500},
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.status_code == 200
    assert response.json() == {"url": "https://checkout.stripe.com/c/pay/cs_test"}
    assert asked == [(1500, "http://localhost:5173/settings")]
    # Below $5 or above $500 is refused before Stripe is asked.
    assert client.post("/api/v1/me/billing/top-up", json={"amount_cents": 499}).status_code == 422
    assert (
        client.post("/api/v1/me/billing/top-up", json={"amount_cents": 50_001}).status_code == 422
    )


@pytest.mark.usefixtures("billed")
def test_a_second_subscription_is_refused(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_account(user, subscription="active")

    response = client.post("/api/v1/me/billing/subscription", json={"interval": "year"})

    assert response.status_code == 409


def test_an_unknown_site_returns_to_the_public_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "public_url", "https://pyqxel.example")

    assert billing_route._return_url("https://evil.example") == "https://pyqxel.example/settings"
    assert billing_route._return_url(None) == "https://pyqxel.example/settings"


@pytest.mark.usefixtures("billed")
def test_deleting_the_account_ends_billing_and_refunds_credit(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_account(user, subscription="active")
    _send("checkout.session.completed", _top_up_session(1000))
    _charge(user, 2_345_678)  # $2.35 of usage leaves $7.65.
    canceled: list[str] = []
    refunds: list[dict[str, Any]] = []

    async def cancel_async(subscription_id: str, params: dict[str, Any]) -> None:
        canceled.append(subscription_id)

    async def refund_async(params: dict[str, Any], options: dict[str, Any]) -> None:
        refunds.append(params)

    fake = SimpleNamespace(
        v1=SimpleNamespace(
            subscriptions=SimpleNamespace(cancel_async=cancel_async),
            refunds=SimpleNamespace(create_async=refund_async),
        )
    )
    monkeypatch.setattr(stripe_api, "client", lambda: fake)

    assert client.delete("/api/v1/me").status_code == 204

    assert canceled == ["sub_1"]
    assert refunds == [{"payment_intent": "pi_1", "amount": 765, "reason": "requested_by_customer"}]
    assert _run(_account(user.id)) is None


def test_recent_usage_counts_only_the_last_30_days(user: User) -> None:
    _make_account(user, credit=MICROS)
    _charge(user, 1000)

    async def since(days: int) -> tuple[int, int]:
        async with get_sessionmaker()() as session:
            return await billing.usage_since(
                session, user.id, datetime.now(timezone.utc) + timedelta(days=days)
            )

    assert _run(since(-30)) == (1000, 1)
    assert _run(since(1)) == (0, 0)
