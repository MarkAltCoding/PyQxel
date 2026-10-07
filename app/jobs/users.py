"""Create accounts and issue API keys from the command line.

There is no public sign-up: whoever runs the server creates accounts::

    python -m app.jobs.users generate-encryption-key
    python -m app.jobs.users create jane@example.com
    python -m app.jobs.users create me@example.com --import-env-keys --claim-unowned
    python -m app.jobs.users add-key jane@example.com --name laptop

``create`` and ``add-key`` print the new API key once; give it to the account's owner.
Owners then store their own provider keys with ``PUT /api/v1/me/credentials``.

``--import-env-keys`` copies ``ANTHROPIC_API_KEY`` and ``FINANCIAL_DATA_API_KEY`` from
the server's settings into the new account, for the server owner's own account only:
anyone with that account's API key spends on those keys.
"""

import argparse
import asyncio
from collections.abc import Coroutine
from typing import Any

from app.core.config import get_settings
from app.core.security import generate_encryption_key
from app.db.session import close_database, get_sessionmaker, init_db
from app.db.users import (
    EmailTakenError,
    claim_unowned,
    create_api_key,
    create_user,
    find_user_by_email,
    update_credentials,
)
from app.models.account import CredentialsUpdate


async def _create(email: str, key_name: str, import_env_keys: bool, claim: bool) -> None:
    """Create the account, optionally with the server's provider keys and old results."""
    await init_db()
    async with get_sessionmaker()() as session:
        try:
            user = await create_user(session, email)
        except EmailTakenError as exc:
            raise SystemExit(str(exc)) from exc
        if import_env_keys:
            settings = get_settings()
            changes = CredentialsUpdate.model_validate(
                {
                    name: value
                    for name, value in (
                        ("anthropic_api_key", settings.anthropic_api_key),
                        ("fmp_api_key", settings.financial_data_api_key),
                    )
                    if value is not None
                }
            )
            await update_credentials(session, user.id, changes)
            print(f"Imported provider keys: {', '.join(sorted(changes.model_fields_set))}.")
        if claim:
            claimed = await claim_unowned(session, user.id)
            counts = ", ".join(f"{count} {table}" for table, count in claimed.items())
            print(f"Claimed unowned results: {counts}.")
        issued = await create_api_key(session, user.id, key_name)
    print(f"Created account {user.email} ({user.id}).")
    print(f"API key (shown once): {issued.key}")


async def _add_key(email: str, key_name: str) -> None:
    """Issue another API key for an existing account."""
    await init_db()
    async with get_sessionmaker()() as session:
        user = await find_user_by_email(session, email)
        if user is None:
            raise SystemExit(f"No account uses {email}.")
        issued = await create_api_key(session, user.id, key_name)
    print(f"API key for {user.email} (shown once): {issued.key}")


async def _run(task: Coroutine[Any, Any, None]) -> None:
    """Run ``task``, then close the database."""
    try:
        await task
    finally:
        await close_database()


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Manage PyQxel accounts.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate-encryption-key", help="Print a new CREDENTIALS_ENCRYPTION_KEY.")
    create = commands.add_parser("create", help="Create an account and print its API key.")
    create.add_argument("email")
    create.add_argument("--name", default="default", help="Name of the first API key.")
    create.add_argument(
        "--import-env-keys",
        action="store_true",
        help="Store the server's ANTHROPIC_API_KEY and FINANCIAL_DATA_API_KEY in this "
        "account. Only for the server owner's own account.",
    )
    create.add_argument(
        "--claim-unowned",
        action="store_true",
        help="Give this account the stored results that predate accounts.",
    )
    add_key = commands.add_parser("add-key", help="Issue another API key for an account.")
    add_key.add_argument("email")
    add_key.add_argument("--name", default="default")
    arguments = parser.parse_args()

    if arguments.command == "generate-encryption-key":
        print(generate_encryption_key())
    elif arguments.command == "create":
        asyncio.run(
            _run(
                _create(
                    arguments.email,
                    arguments.name,
                    arguments.import_env_keys,
                    arguments.claim_unowned,
                )
            )
        )
    else:
        asyncio.run(_run(_add_key(arguments.email, arguments.name)))


if __name__ == "__main__":
    main()
