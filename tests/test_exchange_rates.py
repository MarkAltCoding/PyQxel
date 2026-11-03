"""Tests for exchange rates. Fakes stand in for FMP and yfinance."""

from collections.abc import Iterator
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from app.core.cache import configure_cache
from app.core.config import get_settings
from app.core.credentials import ProviderKeys, current_provider_keys, use_provider_keys
from app.data import exchange_rates, fetcher
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
    # As for the admin's own requests: yfinance answers when YFINANCE_FALLBACK is on.
    monkeypatch.setattr(fetcher, "yfinance_fallback_on", lambda: get_settings().yfinance_fallback)
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


# The European Central Bank's reference rates.

ECB_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
    xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <Cube>
    <Cube time='2026-10-30'>
      <Cube currency='USD' rate='1.1200'/>
      <Cube currency='JPY' rate='176.00'/>
      <Cube currency='GBP' rate='0.8400'/>
    </Cube>
  </Cube>
</gesmes:Envelope>"""


@pytest.fixture
def ecb(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Serve :data:`ECB_XML` as the ECB's feed, through the real fetcher; return the URLs
    asked for."""
    requested: list[str] = []
    monkeypatch.setattr(exchange_rates, "fetch_ecb_rates", ECB_FETCH)

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        return httpx2.Response(200, content=ECB_XML)

    real_client = httpx2.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx2.AsyncClient:
        return real_client(transport=httpx2.MockTransport(handler))

    monkeypatch.setattr(httpx2, "AsyncClient", client)
    return requested


ECB_FETCH = exchange_rates.fetch_ecb_rates
"""The real fetcher, kept before the test setup makes the ECB unreachable."""


def test_the_ecb_feed_is_read_with_the_euro_itself() -> None:
    rates = exchange_rates.parse_ecb_rates(ECB_XML)
    assert rates.day.isoformat() == "2026-10-30"
    assert rates.per_euro == {"EUR": 1.0, "USD": 1.12, "JPY": 176.0, "GBP": 0.84}
    with pytest.raises(DataFetchError):
        exchange_rates.parse_ecb_rates(b"<html>Service unavailable</html>")


@pytest.mark.asyncio
async def test_the_ecb_answers_first_with_cross_rates_and_its_credit(
    ecb: list[str], _fake_yfinance: list[tuple[str, str]]
) -> None:
    eur = await exchange_rates.fetch_sourced_exchange_rate("EUR", "USD")
    jpy = await exchange_rates.fetch_sourced_exchange_rate("jpy")
    gbp_in_yen = await exchange_rates.fetch_sourced_exchange_rate("GBP", "JPY")

    assert eur.rate == pytest.approx(1.12)
    assert jpy.rate == pytest.approx(1.12 / 176.0)
    assert gbp_in_yen.rate == pytest.approx(176.0 / 0.84)
    assert eur.source == "the European Central Bank's reference rate for Oct 30, 2026"
    # The ECB publishes rates per euro; any other pair is calculated, and says so.
    assert jpy.source == (
        "a rate worked out from the European Central Bank's euro reference rates for "
        "Oct 30, 2026"
    )
    assert _fake_yfinance == [], "no provider is asked for a pair the ECB publishes"
    assert len(ecb) == 1, "the day's rates are fetched once and reused"


@pytest.mark.asyncio
async def test_currencies_the_ecb_does_not_publish_come_from_the_providers(
    ecb: list[str], _fake_yfinance: list[tuple[str, str]]
) -> None:
    twd = await exchange_rates.fetch_sourced_exchange_rate("TWD")
    assert twd.rate == 0.031 and twd.source == "yfinance's latest quote"
    assert _fake_yfinance == [("TWD", "USD")]


@pytest.mark.asyncio
async def test_when_the_ecb_is_down_the_providers_answer(
    monkeypatch: pytest.MonkeyPatch, _fake_yfinance: list[tuple[str, str]]
) -> None:
    monkeypatch.setattr(exchange_rates, "fetch_ecb_rates", ECB_FETCH)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503)

    real_client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda *args, **kwargs: real_client(transport=httpx2.MockTransport(handler)),
    )

    assert await fetch_exchange_rate("EUR") == 1.12
    assert _fake_yfinance == [("EUR", "USD")]


def test_a_converted_valuation_credits_the_rates_source() -> None:
    from app.api.v1.endpoints.fundamentals import build_fundamentals
    from app.models.stock import TickerInfo
    from tests.financials import sample_financials

    info = TickerInfo(symbol="SAP", price=200.0, market_cap=1e9, currency="USD", source="fmp")
    rate = exchange_rates.ExchangeRate(
        rate=1.12, source="the European Central Bank's reference rate for Oct 30, 2026"
    )
    fundamentals, _ = build_fundamentals(sample_financials(currency="EUR"), info, rate)

    assert fundamentals.valuation is not None
    assert any(
        "European Central Bank's reference rate for Oct 30, 2026 (1.12 USD per EUR)" in note
        for note in fundamentals.valuation.notes
    )
