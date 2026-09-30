"""Shared pytest configuration.

Tests marked ``live`` call real services (yfinance, Financial Modeling Prep, SEC
EDGAR) and run only with ``--live``. Tests marked ``paid`` also make billed Anthropic
requests and run only with ``--paid``. Everything else is offline.
"""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add opt-in flags for tests that use the network or cost money."""
    parser.addoption(
        "--live", action="store_true", help="Run tests that call real market data services."
    )
    parser.addoption(
        "--paid", action="store_true", help="Run tests that make billed Anthropic API requests."
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register the opt-in markers."""
    config.addinivalue_line("markers", "live: calls real market data services; needs --live")
    config.addinivalue_line("markers", "paid: makes billed Anthropic requests; needs --paid")


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
