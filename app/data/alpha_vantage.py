"""Financial statements from Alpha Vantage, for what SEC EDGAR leaves out.

SEC filings remain the source of a company's financials. Alpha Vantage, on the user's
own key (see :mod:`app.core.credentials`), steps in two ways:

* when EDGAR has no statements for the company, or cannot be reached, the financials are
  built from Alpha Vantage's income statement, balance sheet and cash flow statement;
* when the filings leave items out, such as debt or depreciation that a company tags in
  an unrecognized way, Alpha Vantage fills them in, but only if its figures run to the
  same latest period and are in the same currency, so a filled item never pairs figures
  from different quarters.

Alpha Vantage's free plan allows 25 requests a day, so only the statements covering the
missing items are requested, and answers are cached for a day by
:func:`app.data.fetcher.alpha_vantage_get`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.data.fetcher import (
    DataFetchError,
    finnhub_key,
    alpha_vantage_get,
    alpha_vantage_key,
    alpha_vantage_name,
    alpha_vantage_number,
)
from app.data.fundamentals import (
    ANNUAL_DAYS,
    DATE_SLACK_DAYS,
    FISCAL_YEARS,
    QUARTER_DAYS,
    fetch_financials,
)
from app.data.finnhub_financials import fetch_finnhub_financials
from app.data.sec_edgar import FilingFetchError
from app.models.fundamentals import AnnualValue, BalanceItem, Financials, FlowItem

logger = logging.getLogger(__name__)

INCOME, BALANCE, CASH_FLOW = "INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW"
"""The Alpha Vantage functions serving each statement."""

FLOW_FIELDS: dict[str, tuple[str, str]] = {
    "revenue": (INCOME, "totalRevenue"),
    "gross_profit": (INCOME, "grossProfit"),
    "operating_income": (INCOME, "operatingIncome"),
    "net_income": (INCOME, "netIncome"),
    "operating_cash_flow": (CASH_FLOW, "operatingCashflow"),
    "capital_expenditure": (CASH_FLOW, "capitalExpenditures"),
    "depreciation_amortization": (CASH_FLOW, "depreciationDepletionAndAmortization"),
}
"""Statement and field each income and cash flow item is read from."""

DERIVED: dict[str, tuple[str, str, str]] = {
    "free_cash_flow": ("operating_cash_flow", "-", "capital_expenditure"),
    "ebitda": ("operating_income", "+", "depreciation_amortization"),
}
"""Items computed from two others, as for SEC filings."""

BALANCE_FIELDS: dict[str, str] = {
    "cash": "cashAndCashEquivalentsAtCarryingValue",
    "total_debt": "shortLongTermDebtTotal",
    "stockholders_equity": "totalShareholderEquity",
    "shares_outstanding": "commonStockSharesOutstanding",
}
"""Balance sheet field each balance item is read from."""

ITEM_STATEMENTS: dict[str, frozenset[str]] = {
    **{name: frozenset({statement}) for name, (statement, _) in FLOW_FIELDS.items()},
    "free_cash_flow": frozenset({CASH_FLOW}),
    "ebitda": frozenset({INCOME, CASH_FLOW}),
    **{name: frozenset({BALANCE}) for name in BALANCE_FIELDS},
}
"""The statements each item of :class:`Financials` needs."""

QUARTER_GAP_DAYS: tuple[int, int] = (
    QUARTER_DAYS[0] - DATE_SLACK_DAYS,
    QUARTER_DAYS[1] + DATE_SLACK_DAYS,
)
"""Days between consecutive quarter ends, allowing for 52/53-week fiscal calendars."""

Series = dict[date, float]
"""Figures by the date the period they cover ends."""


@dataclass
class Statements:
    """Items read from Alpha Vantage statements, in the currency they report in."""

    currency: str
    flows: dict[str, FlowItem] = field(default_factory=dict)
    balances: dict[str, BalanceItem] = field(default_factory=dict)

    def items(self) -> dict[str, FlowItem | BalanceItem]:
        """Every item read, by its :class:`Financials` field name."""
        return {**self.flows, **self.balances}


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _reports(payload: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """``annualReports`` or ``quarterlyReports`` that have a period end, latest first."""
    reports = [
        report
        for report in payload.get(kind) or []
        if isinstance(report, dict) and _date(report.get("fiscalDateEnding")) is not None
    ]
    return sorted(reports, key=lambda report: str(report["fiscalDateEnding"]), reverse=True)


def _series(
    reports: list[dict[str, Any]], name: str, transform: Callable[[float], float] = float
) -> Series:
    """The figures reported for field ``name``, by period end."""
    series: Series = {}
    for report in reports:
        end, value = _date(report["fiscalDateEnding"]), alpha_vantage_number(report.get(name))
        if end is not None and value is not None:
            series[end] = transform(value)
    return series


def _consecutive_quarters(ends: list[date]) -> bool:
    """Whether ``ends``, latest first, are each about a quarter apart."""
    low, high = QUARTER_GAP_DAYS
    return all(low <= (later - earlier).days <= high for later, earlier in zip(ends, ends[1:]))


def _ttm(quarters: Series, offset: int) -> tuple[date, float] | None:
    """The sum of four consecutive quarters, starting ``offset`` quarters back."""
    ends = sorted(quarters, reverse=True)[offset : offset + 4]
    if len(ends) < 4 or not _consecutive_quarters(ends):
        return None
    return ends[0], sum(quarters[end] for end in ends)


def _growth(current: float | None, previous: float | None) -> float | None:
    """Relative change from ``previous`` to ``current``; null unless ``previous`` is positive."""
    if current is None or previous is None or previous <= 0:
        return None
    return current / previous - 1.0


def _year_apart(later: date, earlier: date) -> bool:
    return ANNUAL_DAYS[0] <= (later - earlier).days <= ANNUAL_DAYS[1]


def _flow_item(concept: str, annual: Series, quarterly: Series) -> FlowItem | None:
    """A flow item from annual and quarterly figures.

    The TTM total sums the latest four quarters, or, for companies reporting only
    annually, is the latest fiscal year.
    """
    if not annual and not quarterly:
        return None
    years = sorted(annual)
    fiscal_years = [
        AnnualValue(
            fiscal_year_end=end,
            value=annual[end],
            growth=(
                _growth(annual[end], annual[years[index - 1]])
                if index and _year_apart(end, years[index - 1])
                else None
            ),
        )
        for index, end in enumerate(years)
    ][-FISCAL_YEARS:]
    ttm_end = ttm = growth = None
    latest = _ttm(quarterly, 0)
    if latest is not None:
        ttm_end, ttm = latest
        earlier = _ttm(quarterly, 4)
        if earlier is not None and _year_apart(ttm_end, earlier[0]):
            growth = _growth(ttm, earlier[1])
    elif fiscal_years:
        ttm_end, ttm, growth = (
            fiscal_years[-1].fiscal_year_end,
            fiscal_years[-1].value,
            fiscal_years[-1].growth,
        )
    if ttm is None and not fiscal_years:
        return None
    return FlowItem(
        concept=concept, ttm=ttm, ttm_end=ttm_end, ttm_growth=growth, fiscal_years=fiscal_years
    )


def _balance_item(
    concept: str, latest_first: list[dict[str, Any]], name: str
) -> BalanceItem | None:
    """The latest balance sheet figure of ``name``, with the one about a year before."""
    series = _series(latest_first, name)
    if not series:
        return None
    as_of = max(series)
    year_ago = next((value for end, value in series.items() if _year_apart(as_of, end)), None)
    return BalanceItem(concept=concept, value=series[as_of], as_of=as_of, year_ago=year_ago)


def _currency(payload: dict[str, Any]) -> str | None:
    for kind in ("quarterlyReports", "annualReports"):
        reports = _reports(payload, kind)
        if reports and reports[0].get("reportedCurrency"):
            return str(reports[0]["reportedCurrency"]).upper()
    return None


def parse_statements(payloads: dict[str, dict[str, Any]]) -> Statements | None:
    """Read items from Alpha Vantage statement answers, keyed by function.

    Statements in a different currency from the first are left out, so every item is in
    one currency. Returns ``None`` when no statement has any reports.
    """
    currencies = {function: _currency(payload) for function, payload in payloads.items()}
    currency = next((code for code in currencies.values() if code is not None), None)
    if currency is None:
        return None
    usable = {f: p for f, p in payloads.items() if currencies[f] == currency}
    statements = Statements(currency=currency)
    series: dict[str, tuple[Series, Series]] = {}
    for name, (function, source) in FLOW_FIELDS.items():
        payload = usable.get(function)
        if payload is None:
            continue
        # Alpha Vantage reports capital expenditure with either sign; it is kept positive.
        transform: Callable[[float], float] = abs if name == "capital_expenditure" else float
        annual = _series(_reports(payload, "annualReports"), source, transform)
        quarterly = _series(_reports(payload, "quarterlyReports"), source, transform)
        series[name] = (annual, quarterly)
        item = _flow_item(f"alpha_vantage:{source}", annual, quarterly)
        if item is not None:
            statements.flows[name] = item
    for name, (first, sign, second) in DERIVED.items():
        if first not in series or second not in series:
            continue
        combine: Callable[[float, float], float] = (
            (lambda a, b: a - b) if sign == "-" else (lambda a, b: a + b)
        )
        combined = [
            {end: combine(left[end], right[end]) for end in left.keys() & right.keys()}
            for left, right in zip(series[first], series[second])
        ]
        concept = (
            f"alpha_vantage:{FLOW_FIELDS[first][1]} {sign} alpha_vantage:{FLOW_FIELDS[second][1]}"
        )
        item = _flow_item(concept, combined[0], combined[1])
        if item is not None:
            statements.flows[name] = item
    balance = usable.get(BALANCE)
    if balance is not None:
        reports = _reports(balance, "quarterlyReports") or _reports(balance, "annualReports")
        for name, source in BALANCE_FIELDS.items():
            balance_item = _balance_item(f"alpha_vantage:{source}", reports, source)
            if balance_item is not None:
                statements.balances[name] = balance_item
    return statements


def _last_date(item: FlowItem | BalanceItem) -> date | None:
    if isinstance(item, BalanceItem):
        return item.as_of
    ends = [annual.fiscal_year_end for annual in item.fiscal_years]
    if item.ttm_end is not None:
        ends.append(item.ttm_end)
    return max(ends, default=None)


async def _fetch_statements(
    symbol: str, api_key: str, functions: frozenset[str] | set[str]
) -> Statements | None:
    """Fetch and read ``functions``' statements; ones that fail are left out and logged."""
    payloads: dict[str, dict[str, Any]] = {}
    for function in sorted(functions):
        try:
            payloads[function] = await alpha_vantage_get(function, {"symbol": symbol}, api_key)
        except DataFetchError as exc:
            logger.info("Alpha Vantage %s for %s failed: %s", function, symbol, exc)
    return parse_statements(payloads) if payloads else None


async def fetch_alpha_vantage_financials(symbol: str, api_key: str) -> Financials:
    """Build ``symbol``'s financials entirely from Alpha Vantage.

    Raises:
        DataFetchError: If Alpha Vantage has no statements for ``symbol``.
    """
    statements = await _fetch_statements(symbol, api_key, {INCOME, BALANCE, CASH_FLOW})
    if statements is None or not statements.items():
        raise DataFetchError(f"Alpha Vantage has no financial statements for {symbol!r}.")
    overview: dict[str, Any] = {}
    try:
        overview = await alpha_vantage_get("OVERVIEW", {"symbol": symbol}, api_key)
    except DataFetchError as exc:
        # A free key may not read the overview, but finds the name by symbol search.
        logger.info("Alpha Vantage overview for %s failed: %s", symbol, exc)
        try:
            overview = {"Name": (await alpha_vantage_name(symbol, api_key))[0]}
        except DataFetchError:
            pass
    cik = alpha_vantage_number(overview.get("CIK"))
    items: dict[str, Any] = statements.items()
    reached = [day for item in items.values() if (day := _last_date(item))]
    return Financials(
        cik=None if cik is None else int(cik),
        entity_name=str(overview.get("Name") or symbol),
        source="alpha_vantage",
        accounting_standard=None,
        currency=statements.currency,
        latest_period_end=max(reached, default=None),
        **items,
    )


def fill_financials(financials: Financials, statements: Statements) -> Financials:
    """Fill the items ``financials`` lacks from ``statements``, where they line up.

    An item is filled only when Alpha Vantage's figures are in the same currency and run
    to within :data:`DATE_SLACK_DAYS` of the filings' latest period.
    """
    latest = financials.latest_period_end
    if latest is None or statements.currency != financials.currency.upper():
        return financials
    filled: dict[str, FlowItem | BalanceItem] = {}
    for name, item in statements.items().items():
        day = _last_date(item)
        if (
            getattr(financials, name) is None
            and day
            and abs((day - latest).days) <= DATE_SLACK_DAYS
        ):
            filled[name] = item
    if not filled:
        return financials
    return financials.model_copy(
        update=filled | {"filled_items": [*financials.filled_items, *sorted(filled)]}
    )


def missing_items(financials: Financials) -> list[str]:
    """The items of ``financials`` Alpha Vantage could fill."""
    return [name for name in ITEM_STATEMENTS if getattr(financials, name) is None]


async def _stand_in(symbol: str, api_key: str | None) -> Financials | None:
    """Financials from Finnhub's relay of the SEC filings, or from Alpha Vantage, when
    EDGAR cannot supply them; ``None`` when neither can."""
    if finnhub_key() is not None:
        try:
            return await fetch_finnhub_financials(symbol)
        except (DataFetchError, FilingFetchError) as exc:
            logger.info("Finnhub could not stand in for EDGAR: %s", exc)
    if api_key is not None:
        try:
            return await fetch_alpha_vantage_financials(symbol, api_key)
        except DataFetchError as exc:
            logger.info("Alpha Vantage could not stand in for EDGAR: %s", exc)
    return None


async def fetch_financials_filled(symbol: str) -> Financials:
    """Fetch ``symbol``'s financials from SEC EDGAR, with Alpha Vantage filling the gaps.

    When EDGAR cannot supply them, the same filings as Finnhub relays them stand in, and
    then Alpha Vantage's statements, for users with keys for them. Without either key
    this is :func:`~app.data.fundamentals.fetch_financials`.

    Raises:
        FilingFetchError: As :func:`~app.data.fundamentals.fetch_financials` does, when
            neither Finnhub nor Alpha Vantage can stand in.
    """
    api_key = alpha_vantage_key()
    try:
        financials = await fetch_financials(symbol)
    except FilingFetchError:
        stand_in = await _stand_in(symbol.strip().upper(), api_key)
        if stand_in is None:
            raise
        return stand_in
    missing = missing_items(financials)
    if api_key is None or not missing:
        return financials
    needed = frozenset().union(*(ITEM_STATEMENTS[name] for name in missing))
    statements = await _fetch_statements(symbol.strip().upper(), api_key, needed)
    return financials if statements is None else fill_financials(financials, statements)
