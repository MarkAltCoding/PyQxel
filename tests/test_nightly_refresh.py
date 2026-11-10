"""The nightly refresh of a user's own screener: opting in and out, the encrypted keys it
stores, when it runs, and the universe it builds from the user's keys alone."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import get_settings
from app.core.credentials import (
    PROVIDER_HEADERS,
    ProviderKeys,
    current_provider_keys,
    use_provider_keys,
)
from app.core.security import decrypt_secret, generate_encryption_key
from app.data import usage
from app.data.fetcher import DataFetchError, ProviderNotConfiguredError
from app.data.screener_metrics import (
    EtfListing,
    parse_etf_listings,
    parse_etf_metrics,
    parse_quote_price,
    parse_screen_metrics,
)
from app.data.index_members import IndexMembers, name_keys
from app.data.universe import Listing
from app.db import nightly as storage
from app.db.screener import replace_listings, save_companies
from app.db.session import get_sessionmaker
from app.db.tables import NightlyRefreshRecord
from app.db.users import User, create_api_key
from app.jobs import nightly as job
from app.main import app
from app.models.nightly import NightlyRefreshSettings, NightlyUniverse
from app.models.screener import ProviderScreenMetrics
from app.stats.screening import user_screen_metrics
from app.stats.sectors import industry_name, sector_for_sic
from tests.conftest import make_user

client = TestClient(app)

pytestmark = pytest.mark.usefixtures("real_auth")

FINNHUB, TIINGO, ANTHROPIC = "finnhub-key-1234", "tiingo-key-5678", "sk-ant-secret-9999"


def _key_for(user: User) -> str:
    async def issue() -> str:
        async with get_sessionmaker()() as session:
            return (await create_api_key(session, user.id, "test")).key

    return asyncio.run(issue())


def _headers(key: str, **provider_keys: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"} | {
        PROVIDER_HEADERS[name]: value for name, value in provider_keys.items()
    }


@pytest.fixture
def encryption(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the server an encryption key, so it can store keys."""
    monkeypatch.setattr(
        get_settings(), "credentials_encryption_key", SecretStr(generate_encryption_key())
    )


@pytest.fixture
def key(user: User) -> str:
    return _key_for(user)


def _stored(user: User) -> NightlyRefreshRecord | None:
    async def read() -> NightlyRefreshRecord | None:
        async with get_sessionmaker()() as session:
            return await session.get(NightlyRefreshRecord, user.id)

    return asyncio.run(read())


# Opting in and out.


@pytest.mark.usefixtures("encryption")
def test_opting_in_stores_market_data_keys_encrypted(user: User, key: str) -> None:
    """The market data keys are stored encrypted; the Anthropic key is never stored."""
    response = client.put(
        "/api/v1/me/nightly-refresh",
        headers=_headers(key, finnhub=FINNHUB, tiingo=TIINGO, anthropic=ANTHROPIC),
        json={"refresh_time": "20:30", "time_zone": "America/Chicago"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] and body["available"]
    assert body["providers"] == ["finnhub", "tiingo"]
    assert body["refresh_time"] == "20:30" and body["time_zone"] == "America/Chicago"
    assert body["next_run_at"] is not None and body["latest_run"] is None
    record = _stored(user)
    assert record is not None
    for secret in (FINNHUB, TIINGO, ANTHROPIC):
        assert secret not in record.credentials
    plain = json.loads(decrypt_secret(record.credentials).get_secret_value())
    assert plain == {"finnhub": FINNHUB, "tiingo": TIINGO}


@pytest.mark.usefixtures("encryption")
def test_opting_in_again_replaces_the_stored_keys(user: User, key: str) -> None:
    """A key removed in the browser is removed from storage the next time it is sent."""
    url = "/api/v1/me/nightly-refresh"
    client.put(url, headers=_headers(key, finnhub=FINNHUB, tiingo=TIINGO), json={})
    response = client.put(url, headers=_headers(key, finnhub=FINNHUB), json={})

    assert response.json()["providers"] == ["finnhub"]
    record = _stored(user)
    assert record is not None
    assert TIINGO not in decrypt_secret(record.credentials).get_secret_value()


@pytest.mark.usefixtures("encryption")
def test_opting_in_needs_a_finnhub_key(user: User, key: str) -> None:
    response = client.put(
        "/api/v1/me/nightly-refresh", headers=_headers(key, tiingo=TIINGO), json={}
    )

    assert response.status_code == 422
    assert "Finnhub" in response.json()["detail"]
    assert _stored(user) is None


def test_opting_in_needs_the_server_to_have_an_encryption_key(
    user: User, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Whatever the developer's .env holds.
    monkeypatch.setattr(get_settings(), "credentials_encryption_key", None)
    status = client.get("/api/v1/me/nightly-refresh", headers=_headers(key))
    response = client.put(
        "/api/v1/me/nightly-refresh", headers=_headers(key, finnhub=FINNHUB), json={}
    )

    assert status.status_code == 200 and not status.json()["available"]
    assert response.status_code == 503
    assert _stored(user) is None


@pytest.mark.usefixtures("encryption")
def test_an_unknown_time_zone_or_time_is_refused(key: str) -> None:
    url, headers = "/api/v1/me/nightly-refresh", _headers(key, finnhub=FINNHUB)

    assert client.put(url, headers=headers, json={"time_zone": "Mars/Base"}).status_code == 422
    assert client.put(url, headers=headers, json={"refresh_time": "25:00"}).status_code == 422


@pytest.mark.usefixtures("encryption")
def test_opting_out_deletes_the_stored_keys(user: User, key: str) -> None:
    url = "/api/v1/me/nightly-refresh"
    client.put(url, headers=_headers(key, finnhub=FINNHUB), json={})

    response = client.delete(url, headers=_headers(key))

    assert response.status_code == 200
    assert not response.json()["enabled"] and response.json()["providers"] == []
    assert _stored(user) is None


# When refreshes run.


def test_a_refresh_is_due_at_its_local_time_once_a_day() -> None:
    chicago = "America/Chicago"
    # 19:00 in Chicago on November 3, 2026 is 01:00 UTC on November 4.
    moment = datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc)
    before, after = moment - timedelta(minutes=5), moment + timedelta(minutes=5)

    assert storage.next_run_at("19:00", chicago, before, None) == moment
    assert storage.next_run_at("19:00", chicago, after, None) == moment
    assert storage.next_run_at("19:00", chicago, after, after) == moment + timedelta(days=1)


@pytest.mark.usefixtures("encryption")
def test_due_refreshes_skip_users_already_refreshed_or_running(user: User) -> None:
    # Noon, so that jumping past STALE_AFTER and a day lands on the next calendar day
    # whatever time the test runs.
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    settings = NightlyRefreshSettings(
        refresh_time=(now - timedelta(minutes=1)).strftime("%H:%M"), time_zone="UTC"
    )
    keys = ProviderKeys(finnhub=SecretStr(FINNHUB))

    async def scenario() -> list[list[str]]:
        seen: list[list[str]] = []
        async with get_sessionmaker()() as session:
            await storage.enable(session, user.id, settings, keys)
            due = await storage.due_refreshes(session, now)
            seen.append([refresh.user_id for refresh in due])
            assert due[0].keys.finnhub is not None
            assert due[0].keys.finnhub.get_secret_value() == FINNHUB
            run_id = await storage.start_run(session, user.id, now)
            seen.append([r.user_id for r in await storage.due_refreshes(session, now)])
            await storage.finish_run(session, run_id, stocks=1, priced=0)
            seen.append([r.user_id for r in await storage.due_refreshes(session, now)])
            # A run left running for longer than STALE_AFTER is taken to have stopped.
            later = now + storage.STALE_AFTER + timedelta(days=1)
            await storage.start_run(session, user.id, later - storage.STALE_AFTER * 2)
            seen.append([r.user_id for r in await storage.due_refreshes(session, later)])
        return seen

    first, while_running, after_success, after_stale = asyncio.run(scenario())

    assert first == [user.id]
    assert while_running == [] and after_success == []
    assert after_stale == [user.id]


# Building the user's own universe.


def _history(days: int = 300, start_price: float = 50.0) -> pd.DataFrame:
    index = pd.bdate_range(end=pd.Timestamp("2026-10-30"), periods=days)
    returns = np.random.default_rng(3).normal(0.0005, 0.01, days)
    closes = start_price * np.exp(np.cumsum(returns))
    return pd.DataFrame({"Close": closes, "Volume": np.full(days, 2e6)}, index=index)


def _metrics(market_cap: float) -> ProviderScreenMetrics:
    return ProviderScreenMetrics(
        market_cap=market_cap,
        pe_ratio=25.0,
        gross_margin=0.4,
        return_6m=0.1,
        return_52w=0.2,
        volatility_3m=0.3,
        beta=1.1,
        average_volume=5e6,
    )


LISTINGS = [
    Listing(symbol="BIG", cik=1, name="Big Corp", exchange="Nasdaq"),
    Listing(symbol="MID", cik=2, name="Mid Corp", exchange="NYSE"),
    Listing(symbol="TINY", cik=3, name="Tiny Corp", exchange="NYSE"),
    Listing(symbol="NOPE", cik=4, name="No Metrics Inc", exchange="NYSE"),
    Listing(symbol="DUAL", cik=5, name="Dual Class Corp", exchange="NYSE"),
]


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Stored SEC listings, and fake Finnhub metrics and price histories that record the
    keys they ran on."""
    seen: dict[str, list[str]] = {
        "finnhub_keys": [],
        "yfinance": [],
        "histories": [],
        "quotes": [],
        "etf_metrics": [],
        "etf_lists": [],
    }

    async def seed() -> None:
        async with get_sessionmaker()() as session:
            # DUAL has two share classes, so the SEC's shares cannot price it.
            await replace_listings(session, LISTINGS, {"BIG": 1e8, "MID": 1e7, "TINY": 1e6})
            await save_companies(
                session,
                {
                    1: (7372, "SERVICES-PREPACKAGED SOFTWARE"),
                    2: (2834, "PHARMACEUTICAL PREPARATIONS"),
                },
                datetime.now(timezone.utc),
            )

    asyncio.run(seed())

    async def metrics(symbols: Iterable[str]) -> dict[str, ProviderScreenMetrics]:
        keys = current_provider_keys()
        seen["finnhub_keys"].append(keys.finnhub.get_secret_value() if keys.finnhub else "")
        seen["yfinance"].append(str(keys.yfinance or keys.unlicensed))
        caps = {"BIG": 5e9, "DUAL": 2e9, "MID": 1e9, "TINY": 1e6}
        return {symbol: _metrics(caps[symbol]) for symbol in symbols if symbol in caps}

    async def histories(symbols: Iterable[str], start: date) -> dict[str, pd.DataFrame]:
        ordered = list(symbols)
        seen["histories"].extend(ordered)
        # The key's plan covers only the largest stock tonight.
        return {ordered[0]: _history()}

    async def no_factors(model: str) -> pd.DataFrame:
        raise DataFetchError("Ken French's site is unreachable.")

    async def rates(currencies: set[str]) -> dict[str, float]:
        return {}

    async def quotes(symbols: Iterable[str]) -> dict[str, float]:
        seen["quotes"].extend(symbols)
        return {"DUAL": 40.0, "SPY": 680.0}

    async def etf_listings() -> list[EtfListing]:
        seen["etf_lists"].append("asked")
        # BIG is a stock's ticker, so it is not taken for an ETF too.
        return [
            EtfListing("SPY", "SPDR S&P 500 ETF", "NYSE Arca"),
            EtfListing("THIN", "Thinly Traded ETF", "Nasdaq"),
            EtfListing("BIG", "Not an ETF", "Nasdaq"),
        ]

    async def etf_metrics(symbols: Iterable[str]) -> dict[str, ProviderScreenMetrics]:
        asked = list(symbols)
        seen["etf_metrics"].extend(asked)
        known = {
            "SPY": ProviderScreenMetrics(
                average_volume=6e7, week_52_high=700.0, return_52w=0.13, beta=1.0
            ),
            # Too little trades in it to clear the floor even at its 52-week high.
            "THIN": ProviderScreenMetrics(average_volume=100.0, week_52_high=20.0),
        }
        return {symbol: metrics for symbol, metrics in known.items() if symbol in asked}

    monkeypatch.setattr(job, "fetch_etf_listings", etf_listings)
    monkeypatch.setattr(job, "fetch_etf_metrics", etf_metrics)

    monkeypatch.setattr(job, "fetch_quote_prices", quotes)
    monkeypatch.setattr(job, "fetch_screen_metrics", metrics)
    # A server others use: only the owner's account may see unlicensed data.
    monkeypatch.setattr(get_settings(), "admin_email", "owner@example.invalid")
    monkeypatch.setattr(job, "fetch_histories", histories)
    monkeypatch.setattr(job, "fetch_factors", no_factors)
    monkeypatch.setattr(job, "fetch_exchange_rates", rates)
    monkeypatch.setattr(get_settings(), "screener_min_market_cap", 1e8)
    return seen


def _opt_in(user: User, universe: NightlyUniverse = "all") -> None:
    async def enable() -> None:
        async with get_sessionmaker()() as session:
            await storage.enable(
                session,
                user.id,
                NightlyRefreshSettings(refresh_time="00:00", time_zone="UTC", universe=universe),
                ProviderKeys(finnhub=SecretStr(FINNHUB)),
            )

    asyncio.run(enable())


@pytest.mark.usefixtures("encryption")
def test_a_refresh_builds_the_users_own_universe_from_their_keys(
    user: User, key: str, fakes: dict[str, list[str]]
) -> None:
    _opt_in(user)

    assert asyncio.run(job.refresh_due_users()) == 1

    # Only the user's own key, and nothing unlicensed.
    assert fakes["finnhub_keys"] == [FINNHUB] and fakes["yfinance"] == ["False"]
    # Stocks below the market cap floor are not priced; the largest come first, and the
    # ETFs that could clear the dollar volume floor share the histories.
    assert fakes["etf_metrics"] == ["SPY", "THIN"]
    assert fakes["histories"] == ["BIG", "DUAL", "MID", "SPY"]
    # Only a stock with neither history nor SEC shares takes a quote, and an ETF without
    # history.
    assert fakes["quotes"] == ["DUAL", "SPY"]
    status = client.get("/api/v1/screener/status", headers=_headers(key)).json()
    assert status["own"] and status["stocks"] == 4
    assert status["sectors"] == ["Health Care", "Technology"]
    screened = client.post("/api/v1/screener", headers=_headers(key), json={}).json()
    assert screened["own"]
    stocks = {item["symbol"]: item for item in screened["items"]}
    # An ETF has no market cap, so it follows the stocks.
    assert list(stocks) == ["BIG", "DUAL", "MID", "SPY"]
    spy = stocks["SPY"]
    assert (spy["asset_type"], spy["market_cap"], spy["cik"], spy["sector"]) == (
        "etf",
        None,
        None,
        None,
    )
    assert spy["price"] == 680.0 and spy["avg_dollar_volume"] == pytest.approx(6e7 * 680.0)
    assert (spy["momentum_12_1"], spy["beta_market"], spy["pe_ratio"]) == (0.13, 1.0, None)
    assert stocks["BIG"]["asset_type"] == "stock"
    only_etfs = client.post(
        "/api/v1/screener", headers=_headers(key), json={"asset_types": ["etf"]}
    ).json()
    assert [item["symbol"] for item in only_etfs["items"]] == ["SPY"]
    # A screen on a company figure keeps only companies.
    by_pe = client.post(
        "/api/v1/screener",
        headers=_headers(key),
        json={"filters": [{"field": "pe_ratio", "max": 100}]},
    ).json()
    assert "SPY" not in [item["symbol"] for item in by_pe["items"]]
    assert (stocks["BIG"]["sector"], stocks["BIG"]["industry"]) == (
        "Technology",
        "Services-Prepackaged Software",
    )
    # BIG has price history, so its columns are computed from daily prices.
    assert stocks["BIG"]["max_drawdown"] is not None
    # MID has none: Finnhub's figures stand in, and its price is market cap over shares.
    assert stocks["MID"]["max_drawdown"] is None
    assert (stocks["MID"]["momentum_12_1"], stocks["MID"]["volatility"]) == (0.2, 0.3)
    assert stocks["MID"]["beta_market"] == 1.1
    assert stocks["MID"]["price"] == pytest.approx(1e9 / 1e7)
    assert stocks["MID"]["avg_dollar_volume"] == pytest.approx(5e6 * 100.0)
    assert stocks["MID"]["pe_ratio"] == 25.0 and stocks["MID"]["return_6m"] == 0.1
    assert stocks["DUAL"]["price"] == 40.0
    refresh = client.get("/api/v1/me/nightly-refresh", headers=_headers(key)).json()
    assert refresh["latest_run"]["status"] == "succeeded"
    assert refresh["last_success"]["stocks"] == 4 and refresh["last_success"]["priced"] == 1
    # The refresh does not run again the same day.
    assert asyncio.run(job.refresh_due_users()) == 0


@pytest.mark.usefixtures("encryption")
def test_a_refresh_has_the_same_data_access_as_its_users_requests(
    user: User, fakes: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An account allowed unlicensed data on its own requests has it in its refresh too."""
    monkeypatch.setattr(get_settings(), "admin_email", user.email)
    monkeypatch.setattr(get_settings(), "yfinance_fallback", True)
    _opt_in(user)

    asyncio.run(job.refresh_due_users())

    assert fakes["yfinance"] == ["True"]


@pytest.mark.usefixtures("encryption")
def test_a_users_own_universe_is_shown_to_no_one_else(
    user: User, fakes: dict[str, list[str]]
) -> None:
    _opt_in(user)
    asyncio.run(job.refresh_due_users())
    other = asyncio.run(make_user("bob@example.com"))

    status = client.get("/api/v1/screener/status", headers=_headers(_key_for(other))).json()

    assert not status["own"] and status["stocks"] == 0


@pytest.mark.usefixtures("encryption")
def test_a_failed_refresh_is_recorded_and_the_universe_kept(
    user: User, key: str, fakes: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def rejected(symbols: Iterable[str]) -> dict[str, ProviderScreenMetrics]:
        raise ProviderNotConfiguredError("Finnhub rejected your API key; update it in Settings.")

    _opt_in(user)
    monkeypatch.setattr(job, "fetch_screen_metrics", rejected)

    asyncio.run(job.refresh_due_users())

    refresh = client.get("/api/v1/me/nightly-refresh", headers=_headers(key)).json()
    assert refresh["latest_run"]["status"] == "failed"
    assert "Finnhub rejected" in refresh["latest_run"]["error"]
    assert refresh["last_success"] is None


# Reading Finnhub and combining the sources.


def test_finnhub_basic_financials_are_converted() -> None:
    payload = {
        "metric": {
            "marketCapitalization": 2500.0,
            "peTTM": 18.5,
            "grossMarginTTM": 42.0,
            "26WeekPriceReturnDaily": -5.0,
            "3MonthAverageTradingVolume": 1.5,
            "pfcfShareTTM": 20.0,
        }
    }

    metrics = parse_screen_metrics(payload)

    assert metrics is not None
    assert metrics.market_cap == pytest.approx(2.5e9)
    assert metrics.gross_margin == pytest.approx(0.42)
    assert metrics.return_6m == pytest.approx(-0.05)
    assert metrics.average_volume == pytest.approx(1.5e6)
    assert metrics.fcf_yield == pytest.approx(0.05)
    assert parse_screen_metrics({"metric": {"peTTM": 10}}) is None


def test_price_history_fills_its_columns_and_the_provider_comes_first() -> None:
    provider = _metrics(5e9)
    today = date(2026, 10, 30)

    with_history = user_screen_metrics(provider, _history(), None, None, None, today)
    without_shares = user_screen_metrics(provider, None, None, None, None, today)

    assert with_history is not None and without_shares is None
    assert with_history.momentum_12_1 is not None and with_history.volatility is not None
    assert with_history.pe_ratio == 25.0
    assert with_history.return_6m != provider.return_6m


def test_sic_codes_map_to_sectors_and_readable_industries() -> None:
    assert sector_for_sic(3571) == "Technology"  # computers
    assert sector_for_sic(2834) == "Health Care"  # drugs
    assert sector_for_sic(6798) == "Real Estate"  # REITs, inside the finance range
    assert sector_for_sic(6022) == "Finance"
    assert sector_for_sic(4911) == "Utilities"
    assert sector_for_sic(None) is None and sector_for_sic(9995) is None
    assert industry_name("SERVICES-PREPACKAGED SOFTWARE") == "Services-Prepackaged Software"


def test_finnhub_stand_ins_and_quotes_are_read() -> None:
    metrics = parse_screen_metrics(
        {
            "metric": {
                "marketCapitalization": 1000.0,
                "52WeekPriceReturnDaily": 26.98,
                "3MonthADReturnStd": 24.07,
                "beta": 1.1,
            }
        }
    )

    assert metrics is not None
    assert metrics.return_52w == pytest.approx(0.2698)
    assert metrics.volatility_3m == pytest.approx(0.2407)
    assert metrics.beta == 1.1
    assert parse_quote_price({"c": 151.2}) == 151.2
    assert parse_quote_price({"c": 0, "pc": 0}) is None


# Choosing what the refresh may spend.


@pytest.mark.usefixtures("encryption")
def test_opting_in_stores_only_the_keys_chosen_and_says_what_a_refresh_uses(
    user: User, key: str
) -> None:
    response = client.put(
        "/api/v1/me/nightly-refresh",
        headers=_headers(key, finnhub=FINNHUB, tiingo=TIINGO, eodhd="eodhd-user-key"),
        json={"use_keys": ["finnhub", "tiingo"], "price_history_stocks": 30},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["providers"] == ["finnhub", "tiingo"]
    assert body["price_history_stocks"] == 30
    record = _stored(user)
    assert record is not None
    plain = json.loads(decrypt_secret(record.credentials).get_secret_value())
    assert set(plain) == {"finnhub", "tiingo"}, "the EODHD key is not stored"
    use = {item["provider"]: item["requests"] for item in body["key_use"]}
    # Listings and ETFs assumed, since no refresh has run, and 30 price histories.
    assert use == {"finnhub": 5_000 + 4_600, "tiingo": 30}


@pytest.mark.usefixtures("encryption")
def test_a_refresh_fetches_only_as_many_histories_as_chosen_and_records_its_use(
    user: User, key: str, fakes: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def enable() -> None:
        async with get_sessionmaker()() as session:
            await storage.enable(
                session,
                user.id,
                NightlyRefreshSettings(
                    refresh_time="00:00", time_zone="UTC", price_history_stocks=1
                ),
                ProviderKeys(finnhub=SecretStr(FINNHUB), tiingo=SecretStr(TIINGO)),
            )

    asyncio.run(enable())
    histories = getattr(job, "fetch_histories")

    async def counted(symbols: Iterable[str], start: date) -> dict[str, pd.DataFrame]:
        ordered = list(symbols)
        for symbol in ordered:
            usage.record("tiingo", TIINGO, symbols=[symbol])
        assert not current_provider_keys().store_history, "bulk work is not stored"
        found: dict[str, pd.DataFrame] = await histories(ordered, start)
        return found

    monkeypatch.setattr(job, "fetch_histories", counted)

    assert asyncio.run(job.refresh_due_users()) == 1

    assert fakes["histories"] == ["BIG"]
    refresh = client.get("/api/v1/me/nightly-refresh", headers=_headers(key)).json()
    assert refresh["last_success"]["usage"] == {"tiingo": 1}
    assert {item["provider"]: item["requests"] for item in refresh["key_use"]}["tiingo"] == 1


def test_finnhub_symbol_list_gives_the_exchange_listed_etfs() -> None:
    rows = [
        {"symbol": "VOO", "description": "VANGUARD S&P 500 ETF", "type": "ETP", "mic": "ARCX"},
        {"symbol": "QQQM", "description": "INVESCO NASDAQ 100 ETF", "type": "ETP", "mic": "XNAS"},
        {"symbol": "AAPL", "description": "APPLE INC", "type": "Common Stock", "mic": "XNAS"},
        {"symbol": "OTCETF", "description": "Over the counter", "type": "ETP", "mic": "OOTC"},
        {"symbol": "BAD.WS", "description": "Not a plain ticker", "type": "ETP", "mic": "ARCX"},
    ]

    listings = parse_etf_listings(rows)

    assert listings == [
        EtfListing("QQQM", "INVESCO NASDAQ 100 ETF", "Nasdaq"),
        EtfListing("VOO", "VANGUARD S&P 500 ETF", "NYSE Arca"),
    ]


def test_an_etfs_basic_financials_need_only_a_volume() -> None:
    payload = {
        "metric": {
            "26WeekPriceReturnDaily": 6.4,
            "52WeekPriceReturnDaily": 13.5,
            "3MonthADReturnStd": 10.3,
            "3MonthAverageTradingVolume": 6.8,
            "52WeekHigh": 718.47,
            "beta": 1.02,
        }
    }

    metrics = parse_etf_metrics(payload)

    assert metrics is not None and metrics.market_cap is None
    assert metrics.average_volume == pytest.approx(6.8e6)
    assert metrics.return_52w == pytest.approx(0.135) and metrics.week_52_high == 718.47
    assert parse_etf_metrics({"metric": {"52WeekHigh": 10.0}}) is None


def test_stocks_and_etfs_share_histories_in_proportion() -> None:
    stocks = [f"S{i}" for i in range(6)]
    etfs = ["E0", "E1", "E2"]

    merged = job.interleave(stocks, etfs)

    assert sorted(merged) == sorted(stocks + etfs)
    assert merged[:3].count("E0") + merged[:3].count("S0") == 2
    assert [name for name in merged if name.startswith("E")] == etfs
    assert sum(name.startswith("E") for name in merged[:6]) == 2
    assert job.interleave([], etfs) == etfs and job.interleave(stocks, []) == stocks


def test_every_etf_is_checked_once_a_week() -> None:
    now = datetime(2026, 11, 6, 3, 0, tzinfo=timezone.utc)

    assert job.scans_every_etf(None, now)
    assert not job.scans_every_etf(now - timedelta(days=3), now)
    # A refresh that ran a little later last week still scans tonight.
    assert job.scans_every_etf(now - timedelta(days=6, hours=23, minutes=30), now)


def test_between_weekly_scans_only_the_kept_etfs_are_checked(
    user: User, fakes: dict[str, list[str]]
) -> None:
    use_provider_keys(ProviderKeys(finnhub=SecretStr(FINNHUB), user_id=user.id))
    now = datetime.now(timezone.utc)

    first = asyncio.run(job.build_user_universe(user.id, now, None, None))
    assert first.scanned_etfs and fakes["etf_lists"] == ["asked"]
    assert fakes["etf_metrics"] == ["SPY", "THIN"]

    second = asyncio.run(job.build_user_universe(user.id, now, None, now - timedelta(days=1)))

    # The symbol list is not asked for again, and only SPY, which was kept, is checked.
    assert not second.scanned_etfs and fakes["etf_lists"] == ["asked"]
    assert fakes["etf_metrics"] == ["SPY", "THIN", "SPY"]
    assert second.stocks == first.stocks


@pytest.mark.usefixtures("encryption")
def test_a_refresh_that_scans_every_etf_records_when(
    user: User, fakes: dict[str, list[str]]
) -> None:
    _opt_in(user)

    asyncio.run(job.refresh_due_users())

    async def scanned() -> datetime | None:
        async with get_sessionmaker()() as session:
            record = await session.get(NightlyRefreshRecord, user.id)
            return None if record is None else record.etfs_scanned_at

    assert asyncio.run(scanned()) is not None


# Smaller universes.


@pytest.mark.usefixtures("encryption")
def test_a_smaller_universe_is_stored_and_estimated(user: User, key: str) -> None:
    response = client.put(
        "/api/v1/me/nightly-refresh",
        headers=_headers(key, finnhub=FINNHUB),
        json={"universe": "sp500"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["universe"] == "sp500"
    finnhub = next(item for item in body["key_use"] if item["provider"] == "finnhub")
    assert finnhub["requests"] == 545, "the index's listings, not every stock's"
    preview = client.get(
        "/api/v1/me/nightly-refresh",
        headers=_headers(key, finnhub=FINNHUB),
        params={"use_key": ["finnhub"], "universe": "nasdaq100"},
    ).json()
    assert [item["requests"] for item in preview["key_use"]] == [115]
    refused = client.put(
        "/api/v1/me/nightly-refresh",
        headers=_headers(key, finnhub=FINNHUB),
        json={"universe": "everything"},
    )
    assert refused.status_code == 422


@pytest.mark.usefixtures("encryption")
def test_a_refresh_of_nasdaq_stocks_asks_only_about_them_and_no_etfs(
    user: User, fakes: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    # The fixture's fake, patched onto the job.
    metrics: Callable[[Iterable[str]], Awaitable[dict[str, ProviderScreenMetrics]]] = getattr(
        job, "fetch_screen_metrics"
    )

    async def counted(symbols: Iterable[str]) -> dict[str, ProviderScreenMetrics]:
        symbols = list(symbols)
        asked.extend(symbols)
        return await metrics(symbols)

    monkeypatch.setattr(job, "fetch_screen_metrics", counted)
    _opt_in(user, "nasdaq")

    assert asyncio.run(job.refresh_due_users()) == 1

    assert asked == ["BIG"], "only the stock listed on Nasdaq"
    assert fakes["etf_lists"] == [] and fakes["etf_metrics"] == []
    record = _stored(user)
    assert record is not None and record.etfs_scanned_at is None


@pytest.mark.usefixtures("encryption")
def test_a_refresh_of_an_index_keeps_its_companies_main_share_classes(
    user: User, fakes: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    async def members(index: str, listings: Iterable[Listing]) -> IndexMembers:
        assert index == "sp500"
        # Big Corp's two listings, of which the index holds one class, and Mid Corp.
        chosen = [listing for listing in listings if listing.cik in (1, 2)]
        return IndexMembers(listings=chosen, classes={1: 1, 2: 1})

    async def metrics(symbols: Iterable[str]) -> dict[str, ProviderScreenMetrics]:
        symbols = list(symbols)
        asked.extend(symbols)
        found = {symbol: _metrics(5e9) for symbol in symbols}
        # BIGN is Big Corp's note: a market cap, but hardly traded.
        found["BIGN"] = _metrics(5e9).model_copy(update={"average_volume": 10.0})
        return {symbol: found[symbol] for symbol in symbols}

    async def seed() -> None:
        async with get_sessionmaker()() as session:
            await replace_listings(
                session,
                [*LISTINGS, Listing(symbol="BIGN", cik=1, name="Big Corp", exchange="Nasdaq")],
                {"BIG": 1e8, "MID": 1e7},
            )

    asyncio.run(seed())
    monkeypatch.setattr(job, "index_members", members)
    monkeypatch.setattr(job, "fetch_screen_metrics", metrics)
    _opt_in(user, "sp500")

    assert asyncio.run(job.refresh_due_users()) == 1

    assert sorted(asked) == ["BIG", "BIGN", "MID"]
    assert fakes["etf_metrics"] == []
    key = _key_for(user)
    screened = client.post("/api/v1/screener", headers=_headers(key), json={}).json()
    assert sorted(item["symbol"] for item in screened["items"]) == ["BIG", "MID"]


@pytest.mark.usefixtures("encryption")
def test_a_refresh_of_an_index_the_sec_cannot_give_fails_and_keeps_the_universe(
    user: User, fakes: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unreachable(index: str, listings: Iterable[Listing]) -> IndexMembers:
        raise DataFetchError("The SEC's fund filings could not be reached.")

    monkeypatch.setattr(job, "index_members", unreachable)
    _opt_in(user, "nasdaq100")

    asyncio.run(job.refresh_due_users())

    key = _key_for(user)
    refresh = client.get("/api/v1/me/nightly-refresh", headers=_headers(key)).json()
    assert refresh["latest_run"]["status"] == "failed"
    assert "index's companies could not be read" in refresh["latest_run"]["error"]


def test_index_funds_and_the_sec_write_a_company_alike() -> None:
    pairs = [
        ("Applied Materials, Inc.", "APPLIED MATERIALS INC /DE"),
        ("Exxon Mobil Corp.", "ExxonMobil Holdings Corp"),
        ("McDonald's Corp.", "MCDONALDS CORP"),
        ("TJX Cos., Inc. (The)", "TJX COMPANIES INC /DE/"),
        ("Charles Schwab Corp. (The)", "SCHWAB CHARLES CORP"),
        ("NXP Semiconductors NV", "NXP Semiconductors N.V."),
        ("CRH plc", "CRH PUBLIC LTD CO"),
    ]
    for filed, listed in pairs:
        assert set(name_keys(filed)) & set(name_keys(listed)), (filed, listed)
    assert not set(name_keys("Apple Inc.")) & set(name_keys("Applied Materials Inc"))
