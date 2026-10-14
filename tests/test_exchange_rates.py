"""Tests for exchange rates. Fakes stand in for FMP and yfinance."""

from collections.abc import Iterator
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from app.core.cache import configure_cache
from app.core.config import get_settings
from app.core.credentials import ProviderKeys, current_provider_keys, use_provider_keys
from app.data import exchange_rates
from app.data.exchange_rates import fetch_exchange_rate, fetch_exchange_rates
from app.data.fetcher import DataFetchError, ProviderNotConfiguredError, ProviderPlanError

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


FMP_QUOTES = {"EURUSD": 1.1, "USDTWD": 32.0}
"""Pairs FMP quotes; TWD only against the dollar, as FMP writes it."""


@pytest.fixture
def fmp_requests(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, str]]]:
    """Serve :data:`FMP_QUOTES` on an FMP key, recording each pair and key asked for.

    The plan covers neither way of quoting JPY."""
    requests: list[tuple[str, str]] = []

    async def fake_get(
        path: str, params: dict[str, str], api_key: str, client: httpx2.AsyncClient | None
    ) -> Any:
        assert path == "quote"
        requests.append((params["symbol"], api_key))
        if "JPY" in params["symbol"]:
            raise ProviderPlanError("Not on this plan.")
        price = FMP_QUOTES.get(params["symbol"])
        return [] if price is None else [{"symbol": params["symbol"], "price": price}]

    monkeypatch.setattr(exchange_rates, "fmp_get", fake_get)
    previous = current_provider_keys()
    use_provider_keys(ProviderKeys(fmp=SecretStr("fmp-key")))
    yield requests
    use_provider_keys(previous)


@pytest.mark.asyncio
async def test_fmp_rates_come_first_and_are_inverted_when_needed(
    fmp_requests: list[tuple[str, str]], _fake_yfinance: list[tuple[str, str]]
) -> None:
    """FMP answers on the current key; a pair quoted the other way round is inverted."""
    assert await fetch_exchange_rate("EUR") == 1.1
    assert await fetch_exchange_rate("TWD") == pytest.approx(1 / 32.0)

    assert fmp_requests == [("EURUSD", "fmp-key"), ("TWDUSD", "fmp-key"), ("USDTWD", "fmp-key")]
    assert _fake_yfinance == []


@pytest.mark.asyncio
async def test_pairs_outside_the_plan_fall_back_only_in_development(
    fmp_requests: list[tuple[str, str]],
    _fake_yfinance: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the fallback off, a plan error is reported rather than asking Yahoo."""
    monkeypatch.setitem(RATES, ("JPY", "USD"), 0.0063)
    assert await fetch_exchange_rate("JPY") == 0.0063
    assert _fake_yfinance == [("JPY", "USD")]

    exchange_rates.clear_memory()
    monkeypatch.setattr(get_settings(), "yfinance_fallback", False)
    with pytest.raises(ProviderPlanError):
        await fetch_exchange_rate("JPY")
    assert _fake_yfinance == [("JPY", "USD")]


@pytest.mark.asyncio
async def test_no_key_and_no_fallback_means_no_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "yfinance_fallback", False)

    with pytest.raises(ProviderNotConfiguredError):
        await fetch_exchange_rate("EUR")
