"""Tests for exchange rates. A fake stands in for yfinance."""

from collections.abc import Iterator

import pytest

from app.core.cache import configure_cache
from app.data import exchange_rates
from app.data.exchange_rates import fetch_exchange_rate, fetch_exchange_rates
from app.data.fetcher import DataFetchError

RATES = {("TWD", "USD"): 0.031, ("EUR", "USD"): 1.12}


@pytest.fixture(autouse=True)
def _fake_yfinance(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, str]]]:
    """Serve :data:`RATES` without Redis, recording each lookup."""
    lookups: list[tuple[str, str]] = []

    def fake_rate(base: str, quote: str) -> float:
        lookups.append((base, quote))
        if (base, quote) not in RATES:
            raise DataFetchError(f"No {base}/{quote} exchange rate is available.")
        return RATES[base, quote]

    configure_cache(None)
    exchange_rates.clear_memory()
    monkeypatch.setattr(exchange_rates, "_yfinance_rate", fake_rate)
    yield lookups
    exchange_rates.clear_memory()


@pytest.mark.asyncio
async def test_rates_are_fetched_once_and_reused(_fake_yfinance: list[tuple[str, str]]) -> None:
    """A rate is looked up once, whatever the codes' case, and then served from memory."""
    assert await fetch_exchange_rate("twd") == 0.031
    assert await fetch_exchange_rate("TWD", "usd") == 0.031
    assert _fake_yfinance == [("TWD", "USD")]


@pytest.mark.asyncio
async def test_same_currency_needs_no_lookup(_fake_yfinance: list[tuple[str, str]]) -> None:
    assert await fetch_exchange_rate("USD", "USD") == 1.0
    assert _fake_yfinance == []


@pytest.mark.asyncio
async def test_missing_rates_are_left_out() -> None:
    """One currency without a rate does not stop the others."""
    with pytest.raises(DataFetchError):
        await fetch_exchange_rate("XYZ")

    assert await fetch_exchange_rates(["EUR", "XYZ", "TWD", "EUR"]) == {
        "EUR": 1.12,
        "TWD": 0.031,
    }
