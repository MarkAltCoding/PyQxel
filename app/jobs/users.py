"""Create accounts and issue API keys from the command line.

There is no public sign-up: whoever runs the server creates accounts::

    python -m app.jobs.users create jane@example.com
    python -m app.jobs.users create me@example.com --claim-unowned
    python -m app.jobs.users add-key jane@example.com --name laptop
    python -m app.jobs.users delete jane@example.com

``create`` and ``add-key`` print the new API key once; give it to the account's owner.
``delete`` removes an account and everything it holds, for deletion requests made
without signing in; owners can also delete their own with ``DELETE /api/v1/me``.
Provider keys are never stored: owners send theirs with each request.
"""

import argparse
import asyncio
from collections.abc import Coroutine
from typing import Any

from app.db.session import close_database, get_sessionmaker, init_db
from app.db.users import (
    EmailTakenError,
    claim_unowned,
    create_api_key,
    create_user,
    delete_user,
    find_user_by_email,
)


async def _create(email: str, key_name: str, claim: bool) -> None:
    """Create the account, optionally with the results that predate accounts."""
    await init_db()
    async with get_sessionmaker()() as session:
        try:
            user = await create_user(session, email)
        except EmailTakenError as exc:
            raise SystemExit(str(exc)) from exc
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


async def _delete(email: str, confirmed: bool) -> None:
    """Delete the account using ``email`` and everything it holds, after confirmation."""
    await init_db()
    async with get_sessionmaker()() as session:
        user = await find_user_by_email(session, email)
        if user is None:
            raise SystemExit(f"No account uses {email}.")
        if not confirmed:
            answer = input(f"Delete {user.email} ({user.id}) and everything it holds? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                raise SystemExit("Nothing was deleted.")
        await delete_user(session, user.id)
    print(f"Deleted account {user.email} ({user.id}).")


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
    create = commands.add_parser("create", help="Create an account and print its API key.")
    create.add_argument("email")
    create.add_argument("--name", default="default", help="Name of the first API key.")
    create.add_argument(
        "--claim-unowned",
        action="store_true",
        help="Give this account the stored results that predate accounts.",
    )
    add_key = commands.add_parser("add-key", help="Issue another API key for an account.")
    add_key.add_argument("email")
    add_key.add_argument("--name", default="default")
    delete = commands.add_parser("delete", help="Delete an account and everything it holds.")
    delete.add_argument("email")
    delete.add_argument("--yes", action="store_true", help="Delete without asking to confirm.")
    arguments = parser.parse_args()

    if arguments.command == "create":
        asyncio.run(
            _run(
                _create(
                    arguments.email,
                    arguments.name,
                    arguments.claim_unowned,
                )
            )
        )
    elif arguments.command == "delete":
        asyncio.run(_run(_delete(arguments.email, arguments.yes)))
    else:
        asyncio.run(_run(_add_key(arguments.email, arguments.name)))


if __name__ == "__main__":
    main()
