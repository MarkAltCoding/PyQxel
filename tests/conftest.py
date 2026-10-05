"""Shared pytest configuration.

Tests marked ``live`` call real services (yfinance, Financial Modeling Prep, SEC
EDGAR) and run only with ``--live``. Tests marked ``paid`` also make billed Anthropic
requests and run only with ``--paid``. Everything else is offline.

Every test gets an empty in-memory results database and runs with the Redis cache
off, whatever ``.env`` configures; tests that need a cache install a fake one.
``DATABASE_URL`` is also forced to an in-memory database before anything is imported,
so an app started outside a test's own setup, such as a module-scoped client, cannot
open or migrate the real database either.
"""

import asyncio
import os
from collections.abc import Iterator

import pytest

# Settings read the environment before .env, so this wins over both.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"

from app.core.cache import configure_cache  # noqa: E402
from app.data.factors import clear_factor_memory  # noqa: E402
from app.db.session import close_database, configure_database, init_db  # noqa: E402


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add opt-in flags for tests that use the network or cost money."""
    parser.addoption(
        "--live", action="store_true", help="Run tests that call real market data services."
    )
    parser.addoption(
        "--paid", action="store_true", help="Run tests that make billed Anthropic API requests."
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip live and paid tests unless their flags are given."""
    skips = {
        "live": (config.getoption("--live"), "needs --live (calls real services)"),
        "paid": (config.getoption("--paid"), "needs --paid (billed Anthropic request)"),
    }
    for item in items:
        for marker, (enabled, reason) in skips.items():
            if marker in item.keywords and not enabled:
                item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(autouse=True)
def isolated_storage() -> Iterator[None]:
    """Give each test a fresh in-memory database and no cache."""
    # A module-scoped app may have opened the default engine already.
    asyncio.run(close_database())
    configure_database("sqlite+aiosqlite://")
    asyncio.run(init_db())
    configure_cache(None)
    clear_factor_memory()
    yield
    configure_cache(None)
    clear_factor_memory()
    asyncio.run(close_database())
