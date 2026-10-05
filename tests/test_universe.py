"""Tests for the screened universe's raw data: listings, share counts, prices, market caps."""

from datetime import date
from typing import Any

import httpx2
import pandas as pd
import pytest

from app.data import universe
from app.data.universe import (
    Listing,
    download_prices,
    fetch_market_caps,
    fetch_share_counts,
    is_common_name,
    is_common_stock,
    parse_listings,
    parse_profiles,
)


@pytest.mark.parametrize(
    ("symbol", "common"),
    [
        ("AAPL", True),
        ("BRK-B", True),
        ("GOOGL", True),
        ("JPM-PC", False),  # Preferred series.
        ("ACAHW", False),  # Warrant.
        ("KCACU", False),  # Unit.
        ("ABCDR", False),  # Right.
        ("KCAC-UN", False),
        ("BAD$", False),
    ],
)
def test_common_stock_tickers(symbol: str, common: bool) -> None:
    """Preferreds, warrants, units and rights are told apart from common shares."""
    assert is_common_stock(symbol) is common


def test_listings_keep_exchange_listed_common_stocks() -> None:
    """OTC quotes, unlisted tickers, preferreds and repeated tickers are dropped."""
    payload = {
        "fields": ["cik", "name", "ticker", "exchange"],
        "data": [
            [320193, "Apple Inc.", "AAPL", "Nasdaq"],
            [1067983, "Berkshire Hathaway", "BRK-B", "NYSE"],
            [1067983, "Berkshire Hathaway", "BRK-A", "NYSE"],
            [19617, "JPMorgan Chase", "JPM-PC", "NYSE"],
            [999, "Pink Sheet Co", "PINK", "OTC"],
            [998, "No Exchange Co", "NONE", None],
            [320193, "Apple Inc.", "AAPL", "Nasdaq"],
        ],
    }

    listings = parse_listings(payload)

    assert [listing.symbol for listing in listings] == ["AAPL", "BRK-A", "BRK-B"]
    assert listings[0] == Listing("AAPL", 320193, "Apple Inc.", "Nasdaq")


def _frames(responses: dict[str, httpx2.Response]) -> tuple[httpx2.AsyncClient, list[str]]:
    """A client serving SEC frames by URL, with 404 for the rest, and its requests."""
    requested: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        return responses.get(str(request.url), httpx2.Response(404))

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), requested


def _frame(concept: str, period: str, rows: list[tuple[int, float]]) -> tuple[str, httpx2.Response]:
    taxonomy = "dei" if concept.startswith("Entity") else "us-gaap"
    url = universe.FRAMES_URL.format(taxonomy=taxonomy, concept=concept, period=period)
    data = [{"cik": cik, "val": value, "end": "2026-07-20"} for cik, value in rows]
    return url, httpx2.Response(200, json={"data": data})


@pytest.mark.asyncio
async def test_share_counts_prefer_cover_pages_and_newer_quarters() -> None:
    """Cover-page counts win over balance sheet counts, and newer quarters over older."""
    responses = dict(
        [
            _frame("EntityCommonStockSharesOutstanding", "CY2026Q2I", [(1, 100.0), (2, 50.0)]),
            _frame("EntityCommonStockSharesOutstanding", "CY2026Q3I", [(1, 110.0)]),
            _frame("CommonStockSharesOutstanding", "CY2026Q3I", [(2, 60.0), (3, 70.0)]),
        ]
    )
    client, requested = _frames(responses)

    counts = await fetch_share_counts(client, today=date(2026, 8, 15))

    assert counts == {1: 110.0, 2: 50.0, 3: 70.0}
    assert len(requested) == 6  # Two concepts over three quarters, missing ones skipped.


def _download_frame(symbols: list[str], days: int = 3) -> pd.DataFrame:
    """A frame shaped like yfinance's multi-ticker download."""
    index = pd.bdate_range("2026-01-05", periods=days)
    columns = pd.MultiIndex.from_product([["Close", "Volume"], symbols])
    frame = pd.DataFrame(1.0, index=index, columns=columns)
    for symbol in symbols:
        frame[("Close", symbol)] = [10.0, 11.0, 12.0][:days]
        frame[("Volume", symbol)] = 1_000.0
    return frame


def test_batch_prices_split_the_download_by_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each symbol gets its closes and volumes; symbols without closes are dropped."""
    raw = _download_frame(["AAA", "BBB"])
    raw[("Close", "BBB")] = float("nan")
    monkeypatch.setattr("app.data.universe.yf.download", lambda *args, **kwargs: raw)

    prices = universe._batch_prices(["AAA", "BBB", "CCC"], date(2026, 1, 1))

    assert list(prices) == ["AAA"]
    assert prices["AAA"]["Close"].tolist() == [10.0, 11.0, 12.0]


@pytest.mark.asyncio
async def test_failing_batches_are_retried_then_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A batch that keeps failing is skipped without ending the download."""
    monkeypatch.setattr(universe, "PRICE_BATCH_SIZE", 2)
    attempts: list[list[str]] = []

    def fake_batch(symbols: list[str], start: date) -> dict[str, pd.DataFrame]:
        attempts.append(symbols)
        if "BAD" in symbols:
            raise ConnectionError("throttled")
        return {symbol: pd.DataFrame({"Close": [1.0], "Volume": [1.0]}) for symbol in symbols}

    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(universe, "_batch_prices", fake_batch)
    monkeypatch.setattr("app.data.universe.asyncio.sleep", no_sleep)

    prices = await download_prices(["AAA", "BAD", "CCC"], date(2026, 1, 1))

    assert sorted(prices) == ["CCC"]
    assert attempts.count(["AAA", "BAD"]) == universe.PRICE_RETRIES + 1


@pytest.mark.asyncio
async def test_market_caps_skip_missing_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Symbols without a positive, finite market cap are left out."""
    values: dict[str, Any] = {"AAA": 5e9, "BBB": None, "CCC": float("nan")}
    monkeypatch.setattr(
        universe, "_market_cap", lambda symbol: values[symbol] if symbol == "AAA" else None
    )

    caps = await fetch_market_caps(["AAA", "BBB", "CCC"])

    assert caps == {"AAA": 5e9}


@pytest.mark.asyncio
async def test_failed_market_cap_lookups_are_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Throttled lookups are retried after a pause; symbols without a value are not."""
    calls: list[str] = []
    throttled = {"AAA"}

    def fake_market_cap(symbol: str) -> float | None:
        calls.append(symbol)
        if symbol in throttled:
            throttled.discard(symbol)
            raise universe.LookupFailedError("Invalid Crumb")
        return 5e9 if symbol != "NONE" else None

    pauses: list[float] = []

    async def record_sleep(delay: float) -> None:
        pauses.append(delay)

    monkeypatch.setattr(universe, "_market_cap", fake_market_cap)
    monkeypatch.setattr("app.data.universe.asyncio.sleep", record_sleep)

    caps = await fetch_market_caps(["AAA", "BBB", "NONE"])

    assert caps == {"AAA": 5e9, "BBB": 5e9}
    assert sorted(calls) == ["AAA", "AAA", "BBB", "NONE"]
    assert pauses == [universe.MARKET_CAP_PAUSE_SECONDS]


def test_nasdaq_profiles_use_edgar_share_class_tickers() -> None:
    """``BRK/B`` becomes BRK-B; blank market caps and sectors become null."""
    payload = {
        "data": {
            "rows": [
                {"symbol": "BRK/B", "marketCap": "1109002410633.00", "sector": "", "industry": ""},
                {
                    "symbol": "NVDA",
                    "marketCap": "5,638,195,000,000",
                    "sector": "Technology",
                    "industry": "Semiconductors",
                },
                {"symbol": "BF/B", "marketCap": "", "sector": "Consumer Staples", "industry": ""},
            ]
        }
    }

    profiles = parse_profiles(payload)

    assert profiles["BRK-B"] == universe.Profile("", 1109002410633.0, None, None)
    assert profiles["NVDA"].market_cap == 5.638195e12
    assert profiles["BF-B"] == universe.Profile("", None, "Consumer Staples", None)


@pytest.mark.parametrize(
    ("name", "common"),
    [
        ("Alphabet Inc. Class A Common Stock", True),
        ("Taiwan Semiconductor Manufacturing Company Ltd. American Depositary Shares", True),
        ("United Rentals, Inc. Common Stock", True),
        (
            "Alphabet Inc. Depositary Shares representing a 1/20th Interest in a Share of "
            "Series B Mandatory Convertible Preferred Stock",
            False,
        ),
        ("AT&T Inc. 5.625% Global Notes due 2067", False),
        ("Acme Acquisition Corp. Units", False),
        ("Acme Acquisition Corp. Warrant", False),
    ],
)
def test_common_stock_names(name: str, common: bool) -> None:
    """Preferred depositary shares, notes, units and warrants are told apart by name."""
    assert is_common_name(name) is common
