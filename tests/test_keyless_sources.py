"""Tests for the keyless public sources that stand in for keys: the Treasury's bill rates
for the interest cash earns, and the Federal Reserve's exchange rates. Every source is
faked; no test reaches a service."""

import asyncio
from datetime import date, timedelta

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
