"""Shared pytest configuration.

Tests marked ``live`` call real services (yfinance, Financial Modeling Prep, SEC
EDGAR) and run only with ``--live``. Tests marked ``paid`` also make billed Anthropic
requests and run only with ``--paid``. Everything else is offline.

Every test gets an empty in-memory results database and runs with the Redis cache
off, whatever ``.env`` configures; tests that need a cache install a fake one.
``DATABASE_URL`` is also forced to an in-memory database before anything is imported,
so an app started outside a test's own setup, such as a module-scoped client, cannot
open or migrate the real database either.

Every test is signed in as a test account (the ``user`` fixture) whose provider keys are
the server's ``ANTHROPIC_API_KEY`` and ``FINANCIAL_DATA_API_KEY``, so live and paid tests
use the owner's keys and offline tests a placeholder. Tests of authentication itself use
the ``real_auth`` fixture to send real API keys instead. The yfinance fallback is on, so
live tests reach data the owner's FMP plan excludes.
"""

import asyncio
import os
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet

# Settings read the environment before .env, so these win over both.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ["CREDENTIALS_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["YFINANCE_FALLBACK"] = "true"

from app.api.auth import authenticate  # noqa: E402
from app.core.cache import configure_cache  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.credentials import ProviderKeys, use_provider_keys  # noqa: E402
from app.core.rate_limit import clear_local_counts  # noqa: E402
from app.data.factors import clear_factor_memory  # noqa: E402
from app.data.fetcher import clear_quote_cache  # noqa: E402
from app.db.session import (  # noqa: E402
    close_database,
    configure_database,
    get_sessionmaker,
    init_db,
)
from app.db.users import User, create_user, update_credentials  # noqa: E402
from app.main import app  # noqa: E402
from app.models.account import CredentialsUpdate  # noqa: E402

PLACEHOLDER_ANTHROPIC_KEY: str = "sk-ant-test-placeholder"


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
    clear_quote_cache()
    clear_local_counts()
    yield
    configure_cache(None)
    clear_factor_memory()
    clear_quote_cache()
    asyncio.run(close_database())


async def make_user(email: str, anthropic: str | None, fmp: str | None) -> User:
    """Create an account with the given provider keys in the test database."""
    async with get_sessionmaker()() as session:
        created = await create_user(session, email)
        changes = CredentialsUpdate.model_validate(
            {"anthropic_api_key": anthropic, "fmp_api_key": fmp}
        )
        return await update_credentials(session, created.id, changes)


@pytest.fixture(autouse=True)
def user(isolated_storage: None) -> Iterator[User]:
    """Sign every request in as a test account, without sending an API key."""
    settings = get_settings()
    anthropic = settings.anthropic_api_key
    fmp = settings.financial_data_api_key
    account = asyncio.run(
        make_user(
            "tester@example.com",
            anthropic.get_secret_value() if anthropic else PLACEHOLDER_ANTHROPIC_KEY,
            fmp.get_secret_value() if fmp else None,
        )
    )

    async def signed_in() -> User:
        use_provider_keys(ProviderKeys(fmp=account.fmp_key()))
        return account

    app.dependency_overrides[authenticate] = signed_in
    yield account
    app.dependency_overrides.pop(authenticate, None)


@pytest.fixture
def real_auth(user: User) -> None:
    """Authenticate requests by the API keys they send, as in production."""
    app.dependency_overrides.pop(authenticate, None)
