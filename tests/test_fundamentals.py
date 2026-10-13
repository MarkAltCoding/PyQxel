"""Tests for reading and normalizing financial statements from SEC company facts.

Company facts documents are built by hand, and a mock transport stands in for sec.gov.
"""

from collections.abc import Iterator
from datetime import date
from typing import Any

import httpx2
import pytest
from fakeredis import FakeAsyncRedis

from app.core.cache import configure_cache
from app.core.config import Settings
from app.data import fundamentals, sec_edgar
from app.data.fundamentals import (
    FinancialsNotFoundError,
    fetch_financials,
    normalize,
    trailing_twelve_months,
)
from app.data.sec_edgar import EdgarNotConfiguredError, FilingFetchError

CIK = 320193

# A fiscal year ending on the last Saturday of September, as Apple's does.
FY2024 = ("2023-10-01", "2024-09-28")
FY2025 = ("2024-09-29", "2025-09-27")
NINE_MONTHS_2025 = ("2024-09-29", "2025-06-28")
NINE_MONTHS_2026 = ("2025-09-28", "2026-06-27")
NINE_MONTHS_2024 = ("2023-10-01", "2024-06-29")
QUARTER_2026 = ("2026-03-29", "2026-06-27")


def fact(
    period: tuple[str, str] | str,
    value: float,
    filed: str = "2026-08-01",
    form: str = "10-Q",
) -> dict[str, Any]:
    """One reported figure, for a period (start, end) or at a date."""
    row: dict[str, Any] = {"val": value, "filed": filed, "form": form, "accn": "0000-00"}
    if isinstance(period, tuple):
        row["start"], row["end"] = period
    else:
        row["end"] = period
    return row


def company(
    us_gaap: dict[str, list[dict[str, Any]]],
    dei: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """A company facts document holding ``us_gaap`` figures in dollars (shares for counts)."""

    def concepts(facts: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        return {
            name: {"units": {"shares" if "Shares" in name else "USD": rows}}
            for name, rows in facts.items()
        }

    return {
        "cik": CIK,
        "entityName": "Apple Inc.",
        "facts": {"us-gaap": concepts(us_gaap), "dei": concepts(dei or {})},
    }


def ifrs_company(
    units: dict[str, dict[str, list[dict[str, Any]]]],
    us_gaap: dict[str, dict[str, list[dict[str, Any]]]] | None = None,
) -> dict[str, Any]:
    """A company facts document holding IFRS (and optionally US GAAP) figures by unit."""
    return {
        "cik": CIK,
        "entityName": "Taiwan Semiconductor Manufacturing Co., Ltd.",
        "facts": {
            "ifrs-full": {name: {"units": rows} for name, rows in units.items()},
            "us-gaap": {name: {"units": rows} for name, rows in (us_gaap or {}).items()},
        },
    }


EQUITY = {"StockholdersEquity": [fact("2026-06-27", 300.0), fact("2025-06-28", 200.0)]}
CALENDAR_2024 = ("2024-01-01", "2024-12-31")
CALENDAR_2025 = ("2025-01-01", "2025-12-31")


def test_ttm_adds_year_to_date_to_the_last_fiscal_year() -> None:
    """TTM = last fiscal year + this year to date - the same span a year earlier."""
    revenue = [
        fact(FY2024, 380.0, form="10-K"),
        fact(FY2025, 400.0, form="10-K"),
        fact(NINE_MONTHS_2024, 290.0),
        fact(NINE_MONTHS_2025, 300.0),
        fact(NINE_MONTHS_2026, 330.0),
        fact(QUARTER_2026, 110.0),
    ]

    item = normalize(company({"Revenues": revenue} | EQUITY), CIK).revenue

    assert item is not None
    assert item.ttm == 430.0
    assert str(item.ttm_end) == "2026-06-27"
    # A year earlier: 380 + 300 - 290 = 390.
    assert item.ttm_growth == pytest.approx(430.0 / 390.0 - 1)
    assert [(str(y.fiscal_year_end), y.value) for y in item.fiscal_years] == [
        ("2024-09-28", 380.0),
        ("2025-09-27", 400.0),
    ]
    assert item.fiscal_years[1].growth == pytest.approx(400.0 / 380.0 - 1)


def test_ttm_is_the_fiscal_year_when_it_ends_last() -> None:
    """Right after a 10-K, the fiscal year is the TTM figure."""
    series = {
        (date(2024, 9, 29), date(2025, 9, 27)): 400.0,
        (date(2025, 6, 29), date(2025, 9, 27)): 120.0,
    }

    assert trailing_twelve_months(series, date(2025, 9, 27)) == 400.0


def test_ttm_sums_four_quarters_without_a_fiscal_year() -> None:
    """A company without a full fiscal year on file sums its last four quarters."""
    quarters = [
        (date(2025, 7, 1), date(2025, 9, 30)),
        (date(2025, 10, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 3, 31)),
        (date(2026, 4, 1), date(2026, 6, 30)),
    ]
    series = {period: 10.0 * (index + 1) for index, period in enumerate(quarters)}

    assert trailing_twelve_months(series, date(2026, 6, 30)) == 100.0
    assert trailing_twelve_months(series, date(2026, 3, 31)) is None


def test_restated_figures_use_the_latest_filing() -> None:
    """A period reported again in a later filing takes the later value."""
    revenue = [
        fact(FY2025, 400.0, filed="2025-11-01", form="10-K"),
        fact(FY2025, 395.0, filed="2026-11-01", form="10-K/A"),
    ]

    item = normalize(company({"Revenues": revenue}), CIK).revenue

    assert item is not None and item.ttm == 395.0


def test_figures_outside_periodic_reports_are_ignored() -> None:
    """Figures from 8-Ks and proxy statements are not used."""
    revenue = [
        fact(FY2025, 400.0, form="10-K"),
        fact(("2025-09-28", "2026-09-26"), 999.0, form="8-K"),
        fact(("2025-09-28", "2026-09-26"), 998.0, form="DEF 14A"),
    ]

    item = normalize(company({"Revenues": revenue}), CIK).revenue

    assert item is not None and (item.ttm, str(item.ttm_end)) == (400.0, "2025-09-27")


def test_the_most_recently_reported_concept_is_used_alone() -> None:
    """A bank's net revenue is not mixed with an older gross revenue concept in one TTM."""
    gross = [fact(("2025-01-01", "2025-12-31"), 300.0, form="10-K")]
    net = [
        fact(("2025-01-01", "2025-12-31"), 180.0, form="10-K"),
        fact(("2025-01-01", "2025-06-30"), 85.0),
        fact(("2026-01-01", "2026-06-30"), 100.0),
    ]

    item = normalize(company({"Revenues": gross, "RevenuesNetOfInterestExpense": net}), CIK).revenue

    assert item is not None
    assert item.concept == "RevenuesNetOfInterestExpense"
    assert item.ttm == 195.0
    assert [y.value for y in item.fiscal_years] == [180.0]


def test_older_concepts_fill_earlier_fiscal_years() -> None:
    """Years before a company switched revenue concepts come from the old concept."""
    old = [fact(("2016-10-02", "2017-09-30"), 229.0, form="10-K")]
    new = [
        fact(("2017-10-01", "2018-09-29"), 266.0, form="10-K"),
        fact(("2018-09-30", "2019-09-28"), 260.0, form="10-K"),
    ]

    item = normalize(
        company(
            {"SalesRevenueNet": old, "RevenueFromContractWithCustomerExcludingAssessedTax": new}
        ),
        CIK,
    ).revenue

    assert item is not None
    assert item.concept == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert [y.value for y in item.fiscal_years] == [229.0, 266.0, 260.0]
    assert item.fiscal_years[1].growth == pytest.approx(266.0 / 229.0 - 1)


def test_growth_from_a_loss_is_not_computed() -> None:
    """Growth from a zero or negative figure is meaningless and left null."""
    income = [
        fact(FY2024, -10.0, form="10-K"),
        fact(FY2025, 40.0, form="10-K"),
    ]

    item = normalize(company({"NetIncomeLoss": income}), CIK).net_income

    assert item is not None
    assert [y.growth for y in item.fiscal_years] == [None, None]


def test_free_cash_flow_and_ebitda_are_derived_per_period() -> None:
    """FCF is operating cash flow less capex; EBITDA is operating income plus D&A."""
    facts = {
        "NetCashProvidedByUsedInOperatingActivities": [fact(FY2025, 110.0, form="10-K")],
        "PaymentsToAcquirePropertyPlantAndEquipment": [fact(FY2025, 10.0, form="10-K")],
        "OperatingIncomeLoss": [fact(FY2025, 120.0, form="10-K")],
        "DepreciationDepletionAndAmortization": [fact(FY2025, 30.0, form="10-K")],
    }

    financials = normalize(company(facts), CIK)

    assert financials.free_cash_flow is not None and financials.free_cash_flow.ttm == 100.0
    assert financials.ebitda is not None and financials.ebitda.ttm == 150.0
    assert financials.ebitda.concept == (
        "OperatingIncomeLoss + DepreciationDepletionAndAmortization"
    )


@pytest.mark.parametrize(
    ("debt", "expected", "concept"),
    [
        (
            {
                "LongTermDebtNoncurrent": 80.0,
                "LongTermDebtCurrent": 10.0,
                "CommercialPaper": 5.0,
            },
            95.0,
            "LongTermDebtNoncurrent + LongTermDebtCurrent + CommercialPaper",
        ),
        (
            {"LongTermDebtNoncurrent": 80.0, "DebtCurrent": 15.0, "LongTermDebtCurrent": 10.0},
            95.0,
            "LongTermDebtNoncurrent + DebtCurrent",
        ),
        (
            {"LongTermDebt": 90.0, "ShortTermBorrowings": 5.0},
            95.0,
            "LongTermDebt + ShortTermBorrowings",
        ),
        ({"ShortTermBorrowings": 5.0}, None, None),
    ],
)
def test_total_debt(debt: dict[str, float], expected: float | None, concept: str | None) -> None:
    """Debt adds borrowings to long-term debt, and is unknown without long-term debt."""
    facts = EQUITY | {name: [fact("2026-06-27", value)] for name, value in debt.items()}

    total = normalize(company(facts), CIK).total_debt

    if expected is None:
        assert total is None
    else:
        assert total is not None and (total.value, total.concept) == (expected, concept)


def test_balances_are_read_at_the_latest_balance_sheet_date() -> None:
    """Cash and equity are taken at the latest equity date, with equity a year earlier."""
    facts = EQUITY | {
        "CashAndCashEquivalentsAtCarryingValue": [
            fact("2026-03-28", 70.0),
            fact("2026-06-27", 50.0),
        ],
    }

    financials = normalize(company(facts), CIK)

    assert financials.cash is not None and financials.cash.value == 50.0
    equity = financials.stockholders_equity
    assert equity is not None and (equity.value, equity.year_ago) == (300.0, 200.0)
    assert str(financials.latest_period_end) == "2026-06-27"


def test_cover_page_shares_with_several_classes_are_skipped() -> None:
    """When the cover page lists one count per class, the balance sheet total is used."""
    dei = {
        "EntityCommonStockSharesOutstanding": [
            fact("2026-07-20", 5.0),
            fact("2026-07-20", 7.0),
        ]
    }
    facts = EQUITY | {"CommonStockSharesOutstanding": [fact("2026-06-27", 12.0)]}

    shares = normalize(company(facts, dei), CIK).shares_outstanding

    assert shares is not None
    assert (shares.concept, shares.value) == ("CommonStockSharesOutstanding", 12.0)


def test_cover_page_shares_are_preferred_when_newer() -> None:
    """A single cover-page count, dated after the balance sheet, is the latest count."""
    dei = {"EntityCommonStockSharesOutstanding": [fact("2026-07-20", 11.0)]}
    facts = EQUITY | {"CommonStockSharesOutstanding": [fact("2026-06-27", 12.0)]}

    shares = normalize(company(facts, dei), CIK).shares_outstanding

    assert shares is not None and (shares.value, str(shares.as_of)) == (11.0, "2026-07-20")


def test_items_no_longer_reported_are_left_out() -> None:
    """An item last reported years ago is dropped rather than passed off as current."""
    facts = EQUITY | {
        "NetIncomeLoss": [fact(FY2025, 40.0, form="10-K")],
        "OperatingIncomeLoss": [fact(("2012-01-01", "2012-12-31"), 20.0, form="10-K")],
    }

    financials = normalize(company(facts), CIK)

    assert financials.net_income is not None
    assert financials.operating_income is None


def test_company_without_us_gaap_or_ifrs_figures_is_not_found() -> None:
    """A fund or shell company has none of the items read here."""
    with pytest.raises(FinancialsNotFoundError):
        normalize({"facts": {"ifrs-full": {}, "dei": {}}}, CIK)


def test_ifrs_figures_are_read_in_the_home_currency() -> None:
    """A 20-F's figures are in its own currency, not its convenience dollar translation."""
    annual = "20-F"
    document = ifrs_company(
        {
            "Revenue": {
                "TWD": [
                    fact(CALENDAR_2024, 2_900.0, filed="2025-04-17", form=annual),
                    fact(CALENDAR_2025, 3_800.0, filed="2026-04-16", form=annual),
                ],
                "USD": [fact(CALENDAR_2025, 116.0, filed="2026-04-16", form=annual)],
            },
            "ProfitLoss": {
                "TWD": [fact(CALENDAR_2025, 1_700.0, filed="2026-04-16", form=annual)],
            },
            "EquityAttributableToOwnersOfParent": {
                "TWD": [fact("2025-12-31", 5_000.0, filed="2026-04-16", form=annual)],
            },
            "CashAndCashEquivalents": {
                "TWD": [fact("2025-12-31", 2_100.0, filed="2026-04-16", form=annual)],
            },
        }
    )

    financials = normalize(document, CIK)

    assert (financials.accounting_standard, financials.currency) == ("IFRS", "TWD")
    assert financials.revenue is not None
    assert financials.revenue.concept == "ifrs-full:Revenue"
    assert financials.revenue.ttm == 3_800.0
    assert financials.revenue.ttm_growth == pytest.approx(3_800.0 / 2_900.0 - 1)
    assert financials.net_income is not None and financials.net_income.ttm == 1_700.0
    assert financials.cash is not None and financials.cash.value == 2_100.0
    assert financials.stockholders_equity is not None
    assert financials.total_debt is None


def test_a_switch_to_ifrs_keeps_the_us_gaap_history() -> None:
    """A company that moved from US GAAP to IFRS is read under IFRS, with older years kept."""
    document = ifrs_company(
        {"Revenue": {"JPY": [fact(CALENDAR_2025, 13_000.0, filed="2026-06-20", form="20-F")]}},
        us_gaap={
            "Revenues": {"JPY": [fact(CALENDAR_2024, 12_000.0, filed="2025-06-20", form="20-F")]}
        },
    )

    financials = normalize(document, CIK)

    assert (financials.accounting_standard, financials.currency) == ("IFRS", "JPY")
    assert financials.revenue is not None
    assert financials.revenue.concept == "ifrs-full:Revenue"
    assert [year.value for year in financials.revenue.fiscal_years] == [12_000.0, 13_000.0]


@pytest.mark.parametrize(
    ("borrowings", "expected", "concept"),
    [
        (
            {
                "LongtermBorrowings": 24.7,
                "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings": 3.1,
                "Borrowings": 29.6,
            },
            27.8,
            "ifrs-full:LongtermBorrowings"
            " + ifrs-full:CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
        ),
        (
            {
                "LongtermBorrowings": 2_066.8,
                "CurrentPortionOfLongtermBorrowings": 287.4,
                "ShorttermBorrowings": 1_844.0,
            },
            4_198.2,
            "ifrs-full:LongtermBorrowings + ifrs-full:CurrentPortionOfLongtermBorrowings"
            " + ifrs-full:ShorttermBorrowings",
        ),
        (
            {
                "LongtermBorrowings": 118.9,
                "CurrentPortionOfLongtermBorrowings": 12.0,
                "ShorttermBorrowings": 12.0,
            },
            130.9,
            "ifrs-full:LongtermBorrowings + ifrs-full:CurrentPortionOfLongtermBorrowings",
        ),
        ({"Borrowings": 6.2}, 6.2, "ifrs-full:Borrowings"),
        ({"LeaseLiabilities": 1.0}, None, None),
    ],
)
def test_ifrs_total_debt(
    borrowings: dict[str, float], expected: float | None, concept: str | None
) -> None:
    """Borrowings are noncurrent plus current, counting a doubly tagged line once."""
    balance_date = "2025-12-31"
    units = {
        name: {"EUR": [fact(balance_date, value, form="20-F")]}
        for name, value in borrowings.items()
    }
    units["Equity"] = {"EUR": [fact(balance_date, 100.0, form="20-F")]}

    total = normalize(ifrs_company(units), CIK).total_debt

    if expected is None:
        assert total is None
    else:
        assert total is not None
        assert total.value == pytest.approx(expected)
        assert total.concept == concept


TICKERS = {"0": {"cik_str": CIK, "ticker": "AAPL", "title": "Apple Inc."}}
FACTS_URL = fundamentals.COMPANY_FACTS_URL.format(cik=CIK)
DOCUMENT = company({"Revenues": [fact(FY2025, 400.0, form="10-K")]} | EQUITY)


SUBMISSIONS_URL = sec_edgar.SUBMISSIONS_URL.format(cik=CIK)
FILING_FOLDER = f"https://www.sec.gov/Archives/edgar/data/{CIK}/000032019326000100"
INSTANCE = """<?xml version="1.0" encoding="utf-8"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:us-gaap="http://fasb.org/us-gaap/2025"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi">
  <context id="fy">
    <entity><identifier scheme="http://www.sec.gov/CIK">0000320193</identifier></entity>
    <period><startDate>2025-09-28</startDate><endDate>2026-09-26</endDate></period>
  </context>
  <context id="fy-segment">
    <entity>
      <identifier scheme="http://www.sec.gov/CIK">0000320193</identifier>
      <segment><xbrldi:explicitMember dimension="x">y</xbrldi:explicitMember></segment>
    </entity>
    <period><startDate>2025-09-28</startDate><endDate>2026-09-26</endDate></period>
  </context>
  <context id="end">
    <entity><identifier scheme="http://www.sec.gov/CIK">0000320193</identifier></entity>
    <period><instant>2026-09-26</instant></period>
  </context>
  <unit id="usd"><measure>iso4217:USD</measure></unit>
  <us-gaap:Revenues contextRef="fy" unitRef="usd" decimals="-6">450</us-gaap:Revenues>
  <us-gaap:Revenues contextRef="fy-segment" unitRef="usd" decimals="-6">999</us-gaap:Revenues>
  <us-gaap:StockholdersEquity contextRef="end" unitRef="usd" decimals="-6"
    >320</us-gaap:StockholdersEquity>
</xbrl>"""
"""A 10-K's XBRL instance with a total, a segment's figure and a balance."""
MISSING_FILING = {
    "filings": {
        "recent": {
            "form": ["10-K", "10-Q"],
            "filingDate": ["2026-10-30", "2026-08-01"],
            "accessionNumber": ["0000320193-26-000100", "0000320193-26-000050"],
            "isXBRL": [1, 1],
        }
    }
}
"""Submissions listing a 10-K that the company facts document lacks."""


def _edgar(
    facts: httpx2.Response | None = None, submissions: dict[str, Any] | None = None
) -> tuple[httpx2.AsyncClient, list[str]]:
    """A client for a fake EDGAR serving the ticker map, ``facts`` and, if given,
    ``submissions`` with the 10-K it lists; and the requests made."""
    requested: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        requested.append(url)
        if url == sec_edgar.TICKERS_URL:
            return httpx2.Response(200, json=TICKERS)
        if url == FACTS_URL:
            return facts or httpx2.Response(200, json=DOCUMENT)
        if submissions is not None:
            if url == SUBMISSIONS_URL:
                return httpx2.Response(200, json=submissions)
            if url == f"{FILING_FOLDER}/index.json":
                names = [{"name": "aapl-20260926.htm"}, {"name": "aapl-20260926_htm.xml"}]
                return httpx2.Response(200, json={"directory": {"item": names}})
            if url == f"{FILING_FOLDER}/aapl-20260926_htm.xml":
                return httpx2.Response(200, content=INSTANCE.encode())
        return httpx2.Response(404)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), requested


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start each test with empty caches, an SEC User-Agent and no retry delays."""
    sec_edgar.clear_caches()
    fundamentals.clear_memory()
    monkeypatch.setattr(sec_edgar, "get_settings", lambda: Settings(sec_user_agent="Test t@e.com"))

    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("app.data.sec_edgar.asyncio.sleep", no_sleep)
    yield
    sec_edgar.clear_caches()
    fundamentals.clear_memory()


@pytest.mark.asyncio
async def test_fetch_reads_company_facts_and_reuses_them() -> None:
    """The ticker's CIK picks the document, and a second request is served from memory."""
    client, requested = _edgar()

    first = await fetch_financials(" aapl ", client=client)
    second = await fetch_financials("AAPL", client=client)

    assert first.cik == CIK and first.entity_name == "Apple Inc."
    assert first.revenue is not None and first.revenue.ttm == 400.0
    assert second == first
    assert requested.count(FACTS_URL) == 1


@pytest.mark.asyncio
async def test_fetch_reuses_financials_cached_in_redis() -> None:
    """Another worker's normalized financials are read from Redis instead of EDGAR."""
    configure_cache(FakeAsyncRedis())
    client, _ = _edgar()
    first = await fetch_financials("AAPL", client=client)
    fundamentals.clear_memory()

    broken, requested = _edgar(httpx2.Response(500))
    second = await fetch_financials("AAPL", client=broken)

    assert second == first
    assert FACTS_URL not in requested


@pytest.mark.asyncio
async def test_company_without_xbrl_data_is_not_found() -> None:
    """EDGAR's 404 for a company with no XBRL financials becomes a not-found error."""
    client, _ = _edgar(httpx2.Response(404))

    with pytest.raises(FinancialsNotFoundError, match="AAPL"):
        await fetch_financials("AAPL", client=client)


@pytest.mark.asyncio
async def test_a_filing_missing_from_company_facts_is_read_from_its_instance() -> None:
    """A newer 10-K that company facts left out is read, leaving out segment figures."""
    client, requested = _edgar(submissions=MISSING_FILING)

    financials = await fetch_financials("AAPL", client=client)

    assert str(financials.latest_period_end) == "2026-09-26"
    assert financials.revenue is not None and financials.revenue.ttm == 450.0
    assert financials.stockholders_equity is not None
    assert financials.stockholders_equity.value == 320.0
    # The 10-Q filed with the figures company facts holds is not read again.
    assert not any("000032019326000050" in url for url in requested)


@pytest.mark.asyncio
async def test_a_company_missing_from_company_facts_is_read_from_its_filings() -> None:
    """Without a company facts document, the company's filings alone are read."""
    client, _ = _edgar(httpx2.Response(404), submissions=MISSING_FILING)

    financials = await fetch_financials("AAPL", client=client)

    assert financials.revenue is not None and financials.revenue.ttm == 450.0


@pytest.mark.asyncio
async def test_edgar_failure_is_a_fetch_error() -> None:
    """Server errors that persist through retries become a fetch error."""
    client, requested = _edgar(httpx2.Response(503))

    with pytest.raises(FilingFetchError, match="financial data"):
        await fetch_financials("AAPL", client=client)
    assert requested.count(FACTS_URL) == sec_edgar.MAX_RETRIES + 1


@pytest.mark.asyncio
async def test_fetch_needs_a_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without SEC_USER_AGENT no request is made."""
    monkeypatch.setattr(sec_edgar, "get_settings", lambda: Settings(sec_user_agent=None))

    with pytest.raises(EdgarNotConfiguredError):
        await fetch_financials("AAPL")
