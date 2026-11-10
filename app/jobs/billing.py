"""Set up Stripe for billing, and report usage, from the command line::

    python -m app.jobs.billing setup
    python -m app.jobs.billing setup --webhook-url https://example.com/webhooks/stripe
    python -m app.jobs.billing report

``setup`` makes whatever of PyQxel's catalog the Stripe account under ``STRIPE_SECRET_KEY``
lacks (see :func:`app.billing.stripe_api.setup_catalog`), and is safe to run again. Run it
once in test mode and once in live mode. With ``--webhook-url`` it also makes the webhook
endpoint, or brings an existing one's events up to date, and prints a new endpoint's
signing secret once: store it as ``STRIPE_WEBHOOK_SECRET``. For development, ``stripe
listen --forward-to localhost:8000/webhooks/stripe`` prints a secret to use instead.

``report`` sends Stripe the usage that subscriptions bill and it has not been told of,
which the service also does every hour.
"""

import argparse
import asyncio
from typing import Any

from app.billing.stripe_api import EVENTS, client, report_usage, setup_catalog
from app.core.config import get_settings
from app.db.session import close_database, get_sessionmaker, init_db


async def _webhook(url: str) -> None:
    """Make the webhook endpoint at ``url``, or update the events of the one there."""
    events: Any = list(EVENTS)
    endpoints = await client().v1.webhook_endpoints.list_async({"limit": 100})
    for endpoint in endpoints.data:
        if endpoint.url == url:
            await client().v1.webhook_endpoints.update_async(
                endpoint.id, {"enabled_events": events}
            )
            print(
                f"Webhook endpoint {endpoint.id} at {url} is up to date; its secret is unchanged."
            )
            return
    made = await client().v1.webhook_endpoints.create_async(
        {
            "url": url,
            "enabled_events": events,
            "description": "PyQxel: payments, subscriptions, refunds, and disputes",
        }
    )
    print(f"Made webhook endpoint {made.id} at {url}.")
    print(f"Its signing secret, shown only now; store it as STRIPE_WEBHOOK_SECRET: {made.secret}")


async def _setup(webhook_url: str | None) -> None:
    """Make the catalog, and the webhook endpoint if asked."""
    held = await setup_catalog(get_settings().public_url)
    for name, value in held.items():
        print(f"{name}: {value}")
    if webhook_url:
        await _webhook(webhook_url)


async def _report() -> None:
    """Report subscriptions' unreported usage."""
    await init_db()
    try:
        async with get_sessionmaker()() as session:
            print(f"Reported {await report_usage(session)} usage entries to Stripe.")
    finally:
        await close_database()


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Set up and run PyQxel's Stripe billing.")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="Make the Stripe catalog billing needs.")
    setup.add_argument("--webhook-url", help="Also make the webhook endpoint at this URL.")
    commands.add_parser("report", help="Report subscriptions' usage to Stripe.")
    args = parser.parse_args()
    if args.command == "setup":
        asyncio.run(_setup(args.webhook_url))
    else:
        asyncio.run(_report())


if __name__ == "__main__":
    main()
