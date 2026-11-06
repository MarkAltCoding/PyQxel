"""Tests for the keyless public sources that stand in for keys: the Treasury's bill rates
for the interest cash earns, and the Federal Reserve's exchange rates. Every source is
faked; no test reaches a service."""

import asyncio
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.api.v1.endpoints import backtest as backtest_route
from app.core.credentials import ProviderKeys, use_provider_keys
from app.data import exchange_rates
from app.data.fetcher import DataFetchError
from app.data.treasury import BUSINESS_DAYS, parse_bill_rates, with_bill_rates
from app.models.factors import RISK_FREE

BILLS_CSV = """Date,"4 WEEKS BANK DISCOUNT","4 WEEKS COUPON EQUIVALENT","13 WEEKS COUPON EQUIVALENT"
11/05/2026,3.89,3.96,4.10
11/04/2026,3.85,,4.09
11/03/2026,3.85,3.91,4.08
"""
"""The Treasury's daily bill rates, cut to three days, one without a 4-week rate."""

FED_CSV = """"Series Description","Euro-Area Euro","Japanese Yen","Taiwanese N.T. Dollar"
"Unit:","Currency","Currency","Currency"
"Multiplier:","1","1","1"
"Currency:","EUR","JPY","TWD"
"Unique Identifier: ","H10/H10/RXI$US_N.B.EU","H10/H10/RXI_N.B.JA","H10/H10/RXI_N.B.TA"
"Time Period","RXI$US_N.B.EU","RXI_N.B.JA","RXI_N.B.TA"
2026-10-29,1.1165,157.83,32.53
2026-10-30,1.1163,ND,32.59
"""
"""The Federal Reserve's H.10 download, cut to three currencies; ND marks no rate."""


def test_treasury_bill_rates_are_read_by_date() -> None:
    rates = parse_bill_rates(BILLS_CSV)

    assert rates == {"2026-11-05": 3.96, "2026-11-03": 3.91}
    assert parse_bill_rates("") == {}
    with pytest.raises(DataFetchError):
        parse_bill_rates("Date,Something else\n11/05/2026,1\n")


def test_bill_rates_replace_and_extend_the_factor_rate() -> None:
    days = pd.bdate_range("2026-09-28", "2026-09-30")
    factors = pd.DataFrame({"Mkt-RF": [0.01, 0.02, 0.03], RISK_FREE: [0.0002] * 3}, index=days)
    bills = pd.Series([0.00015, 0.00016], index=pd.DatetimeIndex(["2026-09-30", "2026-10-01"]))

    combined = with_bill_rates(factors, bills)

    rates = combined[RISK_FREE].to_dict()
    # Ken French's before the bills, the bills' after, and past the factors' last day.
    assert [rates[pd.Timestamp(day)] for day in ("2026-09-28", "2026-09-30", "2026-10-01")] == [
        0.0002,
        0.00015,
        0.00016,
    ]
    assert combined["Mkt-RF"].isna().to_list() == [False, False, False, True]
    assert list(with_bill_rates(None, bills).columns) == [RISK_FREE]


def test_cash_interest_needs_no_factor_download_since_2002(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily = (1.04 ** (1 / BUSINESS_DAYS)) - 1
    asked: list[date] = []

    async def bills(start: date) -> pd.Series:
        asked.append(start)
        days = pd.bdate_range("2002-01-02", date.today())
        return pd.Series(daily, index=days)[lambda s: s.index >= pd.Timestamp(start)]

    async def no_factors(model: str) -> pd.DataFrame:
        raise AssertionError("Ken French's library is not needed for a window after 2002.")

    monkeypatch.setattr(backtest_route, "fetch_bill_returns", bills)
    monkeypatch.setattr(backtest_route, "fetch_factors", no_factors)

    factors, notice = asyncio.run(backtest_route.load_factors(None, True, "10y"))

    assert factors is not None and notice is None
    assert factors[RISK_FREE].iloc[-1] == pytest.approx(daily)
    assert asked and asked[0] > date.today() - timedelta(days=3700)


def test_a_window_before_2002_takes_ken_frenchs_rate_for_the_early_years(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def bills(start: date) -> pd.Series:
        return pd.Series(0.0001, index=pd.bdate_range("2002-01-02", "2002-01-10"))

    async def factors(model: str) -> pd.DataFrame:
        days = pd.bdate_range("1990-01-02", "2002-01-10")
        return pd.DataFrame({"Mkt-RF": 0.0, RISK_FREE: 0.0003}, index=days)

    monkeypatch.setattr(backtest_route, "fetch_bill_returns", bills)
    monkeypatch.setattr(backtest_route, "fetch_factors", factors)

    combined, _ = asyncio.run(backtest_route.load_factors(None, True, "max"))

    assert combined is not None
    assert combined.loc["1995-06-01", RISK_FREE] == 0.0003
    assert combined.loc["2002-01-07", RISK_FREE] == 0.0001


def test_fed_rates_are_read_in_dollars_per_unit() -> None:
    rates = exchange_rates.parse_fed_rates(FED_CSV)

    assert rates.day == date(2026, 10, 30)
    assert rates.usd_per["EUR"] == 1.1163  # Quoted in dollars per euro.
    assert rates.usd_per["TWD"] == pytest.approx(1 / 32.59)  # Quoted per dollar.
    assert rates.usd_per["JPY"] == pytest.approx(1 / 157.83)  # Its latest published day.
    cross = rates.cross("TWD", "JPY")
    assert cross is not None and cross.rate == pytest.approx(157.83 / 32.59)
    assert "worked out" in cross.source


def test_a_currency_the_ecb_lacks_spends_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    exchange_rates.clear_memory()
    use_provider_keys(ProviderKeys())  # No key at all.

    async def fed() -> exchange_rates.FedRates:
        return exchange_rates.parse_fed_rates(FED_CSV)

    monkeypatch.setattr(exchange_rates, "fetch_fed_rates", fed)

    found = asyncio.run(exchange_rates.fetch_sourced_exchange_rate("TWD", "USD"))

    assert found.rate == pytest.approx(1 / 32.59)
    assert "Federal Reserve Board" in found.source


def test_a_missing_sector_comes_from_the_sec_before_any_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.data import fetcher
    from app.models.stock import TickerInfo

    async def industry(symbol: str) -> tuple[int | None, str | None]:
        assert symbol == "ACME"
        return 7372, "SERVICES-PREPACKAGED SOFTWARE"

    monkeypatch.setattr(fetcher, "_sec_industry", industry)
    use_provider_keys(ProviderKeys())

    info = asyncio.run(
        fetcher._fill_info(TickerInfo(symbol="ACME", name="Acme", source="test"), None)
    )

    assert (info.sector, info.industry) == ("Technology", "Services-Prepackaged Software")
    assert info.filled_fields == ["industry", "sector"]


def test_with_the_factor_library_turned_off_cash_earns_from_2002(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.data.fetcher import SourceTurnedOffError

    async def bills(start: date) -> pd.Series:
        return pd.Series(0.0001, index=pd.bdate_range("2002-01-02", "2002-01-10"))

    async def turned_off(model: str) -> pd.DataFrame:
        raise SourceTurnedOffError("Factor models are not available here.")

    monkeypatch.setattr(backtest_route, "fetch_bill_returns", bills)
    monkeypatch.setattr(backtest_route, "fetch_factors", turned_off)

    factors, notice = asyncio.run(backtest_route.load_factors("ff3", True, "max"))

    assert factors is not None and list(factors.columns) == [RISK_FREE]
    assert notice is not None and "only from 2002" in notice and "not used here" in notice


# Factors estimated from ETFs.


def _etf_closes(daily: dict[str, float], days: pd.DatetimeIndex) -> dict[str, pd.DataFrame]:
    """Close prices of ETFs growing by a steady daily return each."""
    return {
        symbol: pd.DataFrame(
            {"Close": 100.0 * (1 + growth) ** pd.Series(range(len(days)), index=days)}
        )
        for symbol, growth in daily.items()
    }


def test_etfs_stand_in_for_the_factors_when_the_library_is_turned_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.data import etf_factors, factors
    from app.data.fetcher import SourceTurnedOffError

    days = pd.bdate_range("2024-01-01", periods=30, tz="America/New_York")
    growth = {"SPY": 0.001, "IWM": 0.002, "IVV": 0.001, "IWD": 0.0005, "IWF": 0.0015}
    closes = _etf_closes(growth, days)

    async def library(model: str, client: object = None) -> pd.DataFrame:
        raise SourceTurnedOffError("Ken French's library is not used here.")

    async def history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        return closes[symbol]

    async def bills(start: date) -> pd.Series:
        return pd.Series(0.0001, index=days.tz_localize(None))

    monkeypatch.setattr(factors, "_library_factors", library)
    monkeypatch.setattr(etf_factors, "fetch_price_history", history)
    monkeypatch.setattr(etf_factors, "fetch_bill_returns", bills)

    table = asyncio.run(factors.fetch_factors("ff3"))

    assert list(table.columns) == ["Mkt-RF", "SMB", "HML", RISK_FREE]
    assert table.attrs[etf_factors.FACTOR_SOURCE] == etf_factors.ETF_SOURCE
    assert table["Mkt-RF"].iloc[-1] == pytest.approx(0.001 - 0.0001)
    assert table["SMB"].iloc[-1] == pytest.approx(0.002 - 0.001)
    assert table["HML"].iloc[-1] == pytest.approx(0.0005 - 0.0015)
    # The five-factor model's investment factor has no ETF, so the market stands in alone.
    market = asyncio.run(factors.fetch_factors("ff5"))
    assert list(market.columns) == ["Mkt-RF", RISK_FREE]
    assert market.attrs[etf_factors.MARKET_ONLY] is True


def test_with_the_etf_estimates_turned_off_the_market_stands_in_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.data import etf_factors, factors
    from app.data.fetcher import SourceTurnedOffError
    from app.db import app_settings
    from app.stats.factors import fit_factor_model

    days = pd.bdate_range("2024-01-01", periods=200, tz="America/New_York")
    moves = np.random.default_rng(7).normal(0.0005, 0.01, len(days))
    closes = {"SPY": pd.DataFrame({"Close": 100 * np.cumprod(1 + moves)}, index=days)}
    asked: list[str] = []

    async def library(model: str, client: object = None) -> pd.DataFrame:
        raise SourceTurnedOffError("Ken French's library is not used here.")

    async def history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        asked.append(symbol)
        return closes[symbol]

    async def bills(start: date) -> pd.Series:
        return pd.Series(0.0001, index=days.tz_localize(None))

    async def off() -> bool:
        return False

    monkeypatch.setattr(factors, "_library_factors", library)
    monkeypatch.setattr(etf_factors, "fetch_price_history", history)
    monkeypatch.setattr(etf_factors, "fetch_bill_returns", bills)
    monkeypatch.setattr(app_settings, "style_factors_allowed", off)

    table = asyncio.run(factors.fetch_factors("carhart4"))

    assert asked == ["SPY"]  # No size, value or momentum ETF is fetched.
    assert list(table.columns) == ["Mkt-RF", RISK_FREE]
    assert etf_factors.factor_notice(table) == etf_factors.MARKET_ONLY_NOTICE
    noise = np.random.default_rng(8).normal(0, 0.005, len(days))
    stock = pd.Series(100 * np.cumprod(1 + 1.2 * moves + noise), index=days)
    fit = fit_factor_model(stock, table, "carhart4")
    assert [exposure.factor for exposure in fit.exposures] == ["Mkt-RF"]
    assert fit.exposures[0].estimate == pytest.approx(1.2, abs=0.15)


def test_a_backtest_says_when_its_factors_are_etf_estimates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.data.etf_factors import ETF_NOTICE, ETF_SOURCE, FACTOR_SOURCE

    async def estimated(model: str) -> pd.DataFrame:
        table = pd.DataFrame(
            {"Mkt-RF": 0.001, "SMB": 0.0, "HML": 0.0, RISK_FREE: 0.0001},
            index=pd.bdate_range("2024-01-01", periods=5),
        )
        table.attrs[FACTOR_SOURCE] = ETF_SOURCE
        return table

    monkeypatch.setattr(backtest_route, "fetch_factors", estimated)

    _, notice = asyncio.run(backtest_route.load_factors("ff3", False, "1y"))

    assert notice == ETF_NOTICE
