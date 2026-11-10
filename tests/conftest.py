"""Shared pytest configuration.

Tests marked ``live`` call real services (yfinance, Financial Modeling Prep, SEC
EDGAR) and run only with ``--live``. Tests marked ``paid`` also make billed Anthropic
requests and run only with ``--paid``. Everything else is offline.

Every test gets an empty in-memory results database and runs with the Redis cache
off, whatever ``.env`` configures; tests that need a cache install a fake one.
``DATABASE_URL`` is also forced to an in-memory database before anything is imported,
so an app started outside a test's own setup, such as a module-scoped client, cannot
open or migrate the real database either.

Every test is signed in as a test account (the ``user`` fixture) whose requests carry
the server's ``ANTHROPIC_API_KEY`` and ``FINANCIAL_DATA_API_KEY`` as provider keys, so
live and paid tests use the owner's keys and offline tests a placeholder. Tests of
authentication itself use the ``real_auth`` fixture to send real API keys, and provider
keys in their headers, instead. The yfinance fallback is on, so
live tests reach data the owner's FMP plan excludes.
"""

import asyncio
import os
from dataclasses import replace
from collections.abc import Iterator

import pytest
from pydantic import SecretStr

# Settings read the environment before .env, so these win over both.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ["YFINANCE_FALLBACK"] = "true"

from app.api.auth import authenticate, sees_unlicensed_data, uses_yfinance  # noqa: E402
from app.core.cache import configure_cache  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.credentials import ProviderKeys, use_provider_keys  # noqa: E402
from app.core.rate_limit import clear_local_counts  # noqa: E402
from app.data.factors import clear_factor_memory  # noqa: E402
from app.data import exchange_rates  # noqa: E402
from app.data import funds as fund_data  # noqa: E402
from app.data.fetcher import (  # noqa: E402
    DataFetchError,
    SymbolNotFoundError,
    clear_alpha_vantage_memory,
    clear_quote_cache,
)  # noqa: E402
from app.api.v1.endpoints import markets as markets_route  # noqa: E402
from app.api.v1.endpoints import search as search_route  # noqa: E402
from app.data.finnhub import clear_signals_memory  # noqa: E402
from app.data.fomc import FALLBACK_MEETINGS, clear_fomc_memory  # noqa: E402
from app.models.markets import FomcMeeting  # noqa: E402
from app.data.news import clear_news_memory  # noqa: E402
from app.data.usage import clear_usage_memory  # noqa: E402
from app.db.session import (  # noqa: E402
    close_database,
    configure_database,
    get_sessionmaker,
    init_db,
)
from app.db.users import User, create_user  # noqa: E402
from app.main import app  # noqa: E402

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
def no_fed_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve FOMC dates from the built-in list, so no test reaches federalreserve.gov."""

    async def fallback() -> list[FomcMeeting]:
        return FALLBACK_MEETINGS

    monkeypatch.setattr(markets_route, "fetch_fomc_meetings", fallback)


@pytest.fixture(autouse=True)
def no_ecb_rates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ECB's and the Federal Reserve's rates are unreachable, so no test reaches them
    and rates come from the providers; tests of their rates serve them themselves."""

    async def unreachable() -> None:
        raise DataFetchError("The ECB is not reached in tests.")

    monkeypatch.setattr(exchange_rates, "fetch_ecb_rates", unreachable)
    monkeypatch.setattr(exchange_rates, "fetch_fed_rates", unreachable)


@pytest.fixture(autouse=True)
def fresh_app_settings() -> Iterator[None]:
    """Server settings start at their defaults, read again from each test's database."""
    from app.db import app_settings

    app_settings.clear_memory()
    yield
    app_settings.clear_memory()


@pytest.fixture(autouse=True)
def no_sec_industries(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SEC's industry codes are unreachable, so no test reaches sec.gov to fill a
    snapshot's sector; tests of that fill fake it themselves."""
    from app.data import fetcher

    async def unreachable(symbol: str) -> None:
        raise DataFetchError("The SEC is not reached in tests.")

    monkeypatch.setattr(fetcher, "_sec_industry", unreachable)


@pytest.fixture(autouse=True)
def no_edgar_filings(monkeypatch: pytest.MonkeyPatch) -> None:
    """EDGAR's list of a company's filings is unreachable, so no test reaches sec.gov for
    it; tests of it serve it themselves."""
    from app.data import alternative

    async def unreachable(symbol: str, today: object, client: object = None) -> None:
        raise DataFetchError("EDGAR is not reached in tests.")

    monkeypatch.setattr(alternative, "fetch_sec_filings", unreachable)


@pytest.fixture(autouse=True)
def no_treasury(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Treasury's bill rates are unreachable, so no test reaches treasury.gov and cash
    interest comes from the faked factor data; tests of the bill rates serve them."""
    from app.api.v1.endpoints import backtest as backtest_route

    async def unreachable(start: object) -> None:
        raise DataFetchError("The Treasury is not reached in tests.")

    monkeypatch.setattr(backtest_route, "fetch_bill_returns", unreachable)


@pytest.fixture(autouse=True)
def no_fund_filings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SEC's fund filings are unreachable, so no test reaches sec.gov for a fund's
    holdings; tests of them serve them themselves."""

    async def unreachable(symbol: str) -> None:
        raise SymbolNotFoundError(f"{symbol} has no fund filing in tests.")

    monkeypatch.setattr(fund_data, "fetch_fund_filing", unreachable)


@pytest.fixture(autouse=True)
def no_sec_ticker_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """Search finds nothing on the SEC's ticker list, so no test reaches sec.gov; tests
    of that search fake the list themselves."""

    async def nothing(query: str, limit: int) -> list[tuple[str, str]]:
        return []

    monkeypatch.setattr(search_route, "search_tickers", nothing)


@pytest.fixture(autouse=True)
def no_billing(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No test bills through Stripe, or reaches it, unless it turns billing on itself:
    ``.env`` may hold a real Stripe key."""
    from app.billing import stripe_api

    monkeypatch.setattr(get_settings(), "stripe_secret_key", None)
    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", None)
    monkeypatch.setattr(get_settings(), "nightly_billed", False)
    stripe_api.client.cache_clear()
    stripe_api._cached().clear()
    yield


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
    clear_alpha_vantage_memory()
    clear_news_memory()
    clear_signals_memory()
    clear_fomc_memory()
    clear_local_counts()
    clear_usage_memory()
    yield
    configure_cache(None)
    clear_factor_memory()
    clear_quote_cache()
    clear_alpha_vantage_memory()
    clear_news_memory()
    clear_signals_memory()
    clear_fomc_memory()
    asyncio.run(close_database())


async def make_user(email: str) -> User:
    """Create an account in the test database."""
    async with get_sessionmaker()() as session:
        return await create_user(session, email)


@pytest.fixture(autouse=True)
def user(isolated_storage: None) -> Iterator[User]:
    """Sign every request in as a test account, without sending an API key."""
    settings = get_settings()
    anthropic = settings.anthropic_api_key
    fmp = settings.financial_data_api_key
    account = asyncio.run(make_user("tester@example.com"))
    keys = ProviderKeys(anthropic=anthropic or SecretStr(PLACEHOLDER_ANTHROPIC_KEY), fmp=fmp)

    async def signed_in() -> User:
        # Marked as production marks a request, from the settings at the time it is made.
        use_provider_keys(
            replace(
                keys,
                yfinance=uses_yfinance(account),
                unlicensed=sees_unlicensed_data(account),
                user_id=account.id,
            )
        )
        return account

    app.dependency_overrides[authenticate] = signed_in
    yield account
    app.dependency_overrides.pop(authenticate, None)


@pytest.fixture
def real_auth(user: User) -> None:
    """Authenticate requests by the API keys they send, as in production."""
    app.dependency_overrides.pop(authenticate, None)
