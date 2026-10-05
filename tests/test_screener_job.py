"""Tests for the job that refreshes the screened universe. Every data source is faked."""

import asyncio
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


import httpx2
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.data.universe import Listing, Profile
from app.db.screener import finish_run, load_fundamentals, start_run, universe_status
from app.db.session import get_sessionmaker
from app.db.tables import ScreenerRunRecord, ScreenerStockRecord
from app.jobs import screener as job
from app.models.fundamentals import Financials
from tests.financials import sample_financials

LISTINGS = [
    Listing("BIG", 1, "Big Corp", "NYSE"),
    Listing("SMALL", 2, "Small Corp", "Nasdaq"),
    Listing("PENNY", 3, "Penny Corp", "Nasdaq"),
    Listing("THIN", 4, "Thin Corp", "NYSE"),
    Listing("ONE", 5, "One Class Corp", "NYSE"),
    Listing("DUO-A", 6, "Duo Corp", "NYSE"),
    Listing("DUO-B", 6, "Duo Corp", "NYSE"),
    Listing("PREF", 1, "Big Corp", "NYSE"),
]


def _prices(price: float, volume: float, days: int = 280) -> pd.DataFrame:
    index = pd.bdate_range(end=date.today() - timedelta(days=1), periods=days)
    closes = price * np.linspace(0.8, 1.0, days)
    return pd.DataFrame({"Close": closes, "Volume": volume}, index=index)


PRICES = {
    "BIG": _prices(100.0, 1e6),  # $100M a day.
    "SMALL": _prices(10.0, 200_000),  # $2M a day, but a $20M market cap.
    "PENNY": _prices(1.0, 5e6),  # Below the $2 price floor.
    "THIN": _prices(20.0, 10_000),  # $200K a day.
    "ONE": _prices(30.0, 100_000),  # Market cap from SEC shares.
    "DUO-A": _prices(40.0, 100_000),
    "DUO-B": _prices(40.0, 100_000),
    "PREF": _prices(25.0, 1e6),
}
PROFILES = {
    "BIG": Profile("Big Corp Common Stock", 2e12, "Technology", "Semiconductors"),
    "SMALL": Profile("Small Corp Common Stock", 2e7, "Industrials", None),
    "PENNY": Profile("Penny Corp Common Stock", 1e9, None, None),
    "THIN": Profile("Thin Corp Common Stock", None, None, None),
    "PREF": Profile("Big Corp Depositary Shares of Series A Preferred Stock", 5e11, None, None),
}
YFINANCE_CAPS = {"THIN": 1e9}
SEC_SHARES = {5: 1e7, 6: 1e7}


class Fakes:
    """Records which slow steps the job took."""

    def __init__(self) -> None:
        self.bulk_downloads = 0
        self.single_fetches: list[str] = []
        self.yfinance_lookups: list[str] = []


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Fakes]:
    """Replace every data source the job reads."""
    recorded = Fakes()

    @asynccontextmanager
    async def fake_client() -> AsyncIterator[object]:
        yield object()

    async def fake_listings(client: object) -> list[Listing]:
        return LISTINGS

    async def fake_prices(symbols: Iterable[str], start: date) -> dict[str, pd.DataFrame]:
        return {symbol: PRICES[symbol] for symbol in symbols}

    async def fake_profiles() -> dict[str, Profile]:
        return PROFILES

    async def fake_caps(symbols: Iterable[str]) -> dict[str, float]:
        recorded.yfinance_lookups.extend(symbols)
        return {s: YFINANCE_CAPS[s] for s in recorded.yfinance_lookups if s in YFINANCE_CAPS}

    async def fake_shares(client: object) -> dict[int, float]:
        return SEC_SHARES

    async def fake_download(client: object, destination: Path) -> None:
        recorded.bulk_downloads += 1
        destination.write_bytes(b"archive")

    def fake_read(archive: Path, ciks: Iterable[int]) -> Iterator[Financials]:
        assert archive.exists()
        for cik in ciks:
            yield sample_financials(cik=cik)

    async def fake_single(symbol: str, client: object = None) -> Financials:
        recorded.single_fetches.append(symbol)
        return sample_financials(cik=next(item.cik for item in LISTINGS if item.symbol == symbol))

    async def fake_factors(model: str) -> pd.DataFrame:
        dates = pd.bdate_range(end=date.today(), periods=300)
        rng = np.random.default_rng(0)
        table = pd.DataFrame(
            rng.normal(0, 0.01, (300, 4)), index=dates, columns=["Mkt-RF", "SMB", "HML", "Mom"]
        )
        table["RF"] = 0.0
        return table

    monkeypatch.setattr(job, "edgar_client", fake_client)
    monkeypatch.setattr(job, "fetch_listings", fake_listings)
    monkeypatch.setattr(job, "download_prices", fake_prices)
    monkeypatch.setattr(job, "fetch_profiles", fake_profiles)
    monkeypatch.setattr(job, "fetch_market_caps", fake_caps)
    monkeypatch.setattr(job, "fetch_share_counts", fake_shares)
    monkeypatch.setattr(job, "download_bulk_company_facts", fake_download)
    monkeypatch.setattr(job, "read_bulk_financials", fake_read)
    monkeypatch.setattr(job, "fetch_financials", fake_single)
    monkeypatch.setattr(job, "fetch_factors", fake_factors)
    monkeypatch.setattr(job, "ARCHIVE_PATH", tmp_path / "companyfacts.zip")
    yield recorded


async def _stored() -> dict[str, ScreenerStockRecord]:
    async with get_sessionmaker()() as session:
        records = await session.scalars(select(ScreenerStockRecord))
        return {row.symbol: row for row in records}


@pytest.mark.asyncio
async def test_refresh_builds_the_filtered_universe(fakes: Fakes, tmp_path: Path) -> None:
    """Price, liquidity and market cap floors pick the universe; every metric is stored."""
    result = await job.refresh_universe()

    stored = await _stored()
    # SMALL is too small, PENNY too cheap, THIN too thin, PREF is a preferred, and DUO's
    # classes cannot be priced from SEC shares.
    assert sorted(stored) == ["BIG", "ONE"]
    assert (stored["BIG"].market_cap_source, stored["BIG"].market_cap_rank) == ("provider", 1)
    assert (stored["BIG"].sector, stored["BIG"].industry) == ("Technology", "Semiconductors")
    # Nasdaq and SEC shares cover the rest; only stocks they miss are looked up one by one.
    assert sorted(fakes.yfinance_lookups) == ["DUO-A", "DUO-B"]
    one = stored["ONE"]
    assert one.market_cap_source == "sec_shares"
    assert one.market_cap == pytest.approx(30.0 * 1e7)
    assert one.pe_ratio is not None and one.beta_market is not None
    assert one.momentum_12_1 is not None
    assert result.stocks == 2 and result.fundamentals_refreshed
    assert fakes.bulk_downloads == 1
    assert not (tmp_path / "companyfacts.zip").exists()
    async with get_sessionmaker()() as session:
        status = await universe_status(session)
        assert set(await load_fundamentals(session, [1, 5])) == {1, 5}
    assert status.stocks == 2 and status.refreshed_at is not None
    assert status.fundamentals_refreshed_at is not None


@pytest.mark.asyncio
async def test_recent_fundamentals_are_reused_and_new_companies_fetched(fakes: Fakes) -> None:
    """Within the week only companies without stored financials are fetched, one by one."""
    async with get_sessionmaker()() as session:
        run_id = await start_run(session)
        await finish_run(session, run_id, fundamentals_refreshed=True)

    result = await job.refresh_universe()

    assert not result.fundamentals_refreshed
    assert fakes.bulk_downloads == 0
    assert sorted(fakes.single_fetches) == ["BIG", "ONE"]


@pytest.mark.asyncio
async def test_old_fundamentals_are_rebuilt(fakes: Fakes) -> None:
    async with get_sessionmaker()() as session:
        run_id = await start_run(session)
        await finish_run(session, run_id, fundamentals_refreshed=True)
        run = await session.get(ScreenerRunRecord, run_id)
        assert run is not None
        run.started_at = datetime.now(timezone.utc) - timedelta(days=8)
        await session.commit()

    result = await job.refresh_universe()

    assert result.fundamentals_refreshed and fakes.bulk_downloads == 1


@pytest.mark.asyncio
async def test_failed_refresh_keeps_the_universe_and_records_why(
    fakes: Fakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    await job.refresh_universe()

    async def failing_listings(client: object) -> list[Listing]:
        raise ConnectionError("SEC unreachable")

    monkeypatch.setattr(job, "fetch_listings", failing_listings)
    with pytest.raises(ConnectionError):
        await job.refresh_universe()

    async with get_sessionmaker()() as session:
        status = await universe_status(session)
    assert status.stocks == 2
    assert status.last_error == "ConnectionError: SEC unreachable"


@pytest.mark.asyncio
async def test_failed_lookups_reuse_the_last_share_count(
    fakes: Fakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a provider value, a stock keeps its implied share count at today's close."""
    await job.refresh_universe()

    async def unavailable() -> dict[str, Profile]:
        raise httpx2.ConnectError("Nasdaq down")

    async def throttled(symbols: Iterable[str]) -> dict[str, float]:
        return {}

    monkeypatch.setattr(job, "fetch_profiles", unavailable)
    monkeypatch.setattr(job, "fetch_market_caps", throttled)
    await job.refresh_universe()

    big = (await _stored())["BIG"]
    assert big.market_cap_source == "provider"
    assert big.market_cap == pytest.approx(2e12)
    assert big.sector is None


def test_open_sessions_are_dropped_until_prices_settle() -> None:
    """Today's partial bar is dropped during trading hours and kept after the close."""
    index = pd.DatetimeIndex(["2026-10-02", "2026-10-05"]).tz_localize("America/New_York")
    prices = {"AAA": pd.DataFrame({"Close": [1.0, 2.0], "Volume": [1.0, 1.0]}, index=index)}
    open_market = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)  # 11:00 in New York.
    after_close = datetime(2026, 10, 5, 21, 0, tzinfo=timezone.utc)

    assert len(job._completed_sessions(prices, open_market)["AAA"]) == 1
    assert len(job._completed_sessions(prices, after_close)["AAA"]) == 2


def test_command_line_runs_a_refresh(fakes: Fakes, capsys: pytest.CaptureFixture[str]) -> None:
    """``python -m app.jobs.screener`` refreshes and reports what it stored."""
    asyncio.run(job._main(force_fundamentals=False))

    assert "Screened universe refreshed: 2 stocks" in capsys.readouterr().out
