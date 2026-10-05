"""Financial statement figures from SEC EDGAR's XBRL company facts, normalized.

EDGAR's company facts API returns every figure a company has tagged in its filings,
each with the period it covers and the filing it came from. This module picks the
figures for a handful of income statement, cash flow and balance sheet items and turns
them into trailing-twelve-month (TTM) totals, fiscal-year histories and the latest
balance sheet.

Three things make that harder than reading one number:

* **Labels.** Companies tag the same item with different XBRL concepts, and switch
  concepts over time (revenue moved to ``RevenueFromContractWithCustomer...`` when
  ASC 606 took effect in 2018). Each item lists the concepts that can carry it; the one
  the company reported most recently is used, so a TTM total never mixes two concepts.
* **Fiscal years.** Fiscal years end in any month, and the ``fy`` and ``fp`` labels in
  the data describe the filing, not the figure. Periods are therefore read from each
  figure's own start and end dates.
* **Year-to-date figures.** 10-Q cash flow statements report the year to date, and no
  filing reports the fourth quarter alone. TTM is the last fiscal year plus the current
  year to date less the same span a year earlier.

Company facts change only when the company files, so normalized results are cached.
"""

import asyncio
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx2
from pydantic import ValidationError

from app.core.cache import cache_get, cache_set
from app.data.sec_edgar import (
    FilingFetchError,
    cik_for,
    edgar_client,
    edgar_errors,
    edgar_get,
)
from app.models.fundamentals import (
    AnnualValue,
    BalanceItem,
    BalanceItemName,
    Financials,
    FlowItem,
    FlowItemName,
)

logger = logging.getLogger(__name__)

COMPANY_FACTS_URL: str = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

CACHE_TTL_SECONDS: float = 6 * 60 * 60
"""How long normalized financials are reused; they change only when the company files."""

MEMORY_CACHE_SIZE: int = 128
"""Companies whose normalized financials are kept in memory."""

FORMS: frozenset[str] = frozenset(
    {"10-K", "10-K/A", "10-KT", "10-Q", "10-Q/A", "10-QT", "20-F", "20-F/A", "40-F", "40-F/A"}
)
"""Periodic reports whose figures are used; registration statements and 8-Ks are not."""

ANNUAL_DAYS: tuple[int, int] = (350, 380)
"""Lengths of a fiscal year, including 52- and 53-week years."""

QUARTER_DAYS: tuple[int, int] = (84, 98)
"""Lengths of a fiscal quarter, including 13- and 14-week quarters."""

DATE_SLACK_DAYS: int = 10
"""How far apart two period ends may be and still count as the same date a year apart."""

FISCAL_YEARS: int = 5
"""Fiscal years of history kept for each item."""

STALE_AFTER_DAYS: int = 400
"""An item whose latest figure is this much older than the company's latest period is left
out: the company no longer reports it under a recognized concept, and an old figure would
pass for a current one."""

FLOW_CONCEPTS: dict[FlowItemName, tuple[str, ...]] = {
    "revenue": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "RevenuesNetOfInterestExpense",
    ),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": (
        "NetIncomeLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "ProfitLoss",
    ),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capital_expenditure": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ),
    "depreciation_amortization": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "Depreciation",
    ),
}
"""US GAAP concepts that carry each period item, most specific first."""

BALANCE_CONCEPTS: dict[BalanceItemName, tuple[str, ...]] = {
    "cash": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "stockholders_equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
}
"""US GAAP concepts that carry each balance sheet item, most specific first."""

LONG_TERM_DEBT_NONCURRENT: tuple[str, ...] = (
    "LongTermDebtNoncurrent",
    "LongTermDebtAndCapitalLeaseObligations",
)
LONG_TERM_DEBT_TOTAL: str = "LongTermDebt"
"""Long-term debt including its current portion."""
DEBT_CURRENT: str = "DebtCurrent"
"""Current long-term debt and short-term borrowings together."""
CURRENT_DEBT_PARTS: tuple[str, ...] = (
    "LongTermDebtCurrent",
    "ShortTermBorrowings",
    "CommercialPaper",
)
SHORT_TERM_BORROWINGS: tuple[str, ...] = ("ShortTermBorrowings", "CommercialPaper")

COVER_SHARES: str = "EntityCommonStockSharesOutstanding"
"""Shares outstanding on a filing's cover page, in the ``dei`` taxonomy."""
BALANCE_SHARES: str = "CommonStockSharesOutstanding"
"""Shares outstanding on the balance sheet."""


class FinancialsNotFoundError(FilingFetchError):
    """Raised when a company has no US GAAP financial data on EDGAR.

    Foreign private issuers reporting under IFRS, funds and shell companies are examples.
    """


@dataclass(frozen=True)
class Fact:
    """One reported figure: its value, the period it covers, and when it was filed."""

    start: date | None
    end: date
    value: float
    filed: date


Durations = dict[tuple[date, date], float]
"""Figures for periods, keyed by (start, end)."""

Instants = dict[date, float]
"""Figures at dates, keyed by date."""


def _parse_date(value: object) -> date | None:
    """Parse an ISO date, mapping blanks and malformed values to ``None``."""
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _rows(company: dict[str, Any], taxonomy: str, concept: str, unit: str) -> list[Fact]:
    """Return the figures for ``concept`` in ``unit`` from periodic reports."""
    entries = company.get("facts", {}).get(taxonomy, {}).get(concept, {}).get("units", {})
    facts: list[Fact] = []
    for row in entries.get(unit, []):
        end = _parse_date(row.get("end"))
        filed = _parse_date(row.get("filed"))
        value = row.get("val")
        if row.get("form") not in FORMS or end is None or filed is None:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        facts.append(Fact(_parse_date(row.get("start")), end, float(value), filed))
    return facts


def _durations(facts: Iterable[Fact]) -> Durations:
    """Key period figures by period, keeping the latest filing's value for each."""
    latest: dict[tuple[date, date], Fact] = {}
    for fact in facts:
        if fact.start is None:
            continue
        key = (fact.start, fact.end)
        if key not in latest or fact.filed >= latest[key].filed:
            latest[key] = fact
    return {key: fact.value for key, fact in latest.items()}


def _instants(facts: Iterable[Fact]) -> Instants:
    """Key balances by date, keeping the latest filing's value for each."""
    latest: dict[date, Fact] = {}
    for fact in facts:
        if fact.start is not None:
            continue
        if fact.end not in latest or fact.filed >= latest[fact.end].filed:
            latest[fact.end] = fact
    return {end: fact.value for end, fact in latest.items()}


def _length(start: date, end: date) -> int:
    """Length of the period from ``start`` to ``end`` inclusive, in days."""
    return (end - start).days + 1


def _is_annual(start: date, end: date) -> bool:
    """Whether the period is a fiscal year."""
    return ANNUAL_DAYS[0] <= _length(start, end) <= ANNUAL_DAYS[1]


def _near(first: date, second: date, slack: int = DATE_SLACK_DAYS) -> bool:
    """Whether two dates are at most ``slack`` days apart."""
    return abs((first - second).days) <= slack


def _year_before(day: date) -> date:
    """The date 52 weeks before ``day``, which is within days of a calendar year before."""
    return day - timedelta(weeks=52)


def _annual_ending(series: Durations, end: date, slack: int = 0) -> float | None:
    """The fiscal-year figure ending at ``end``, within ``slack`` days."""
    for (start, period_end), value in series.items():
        if _near(period_end, end, slack) and _is_annual(start, period_end):
            return value
    return None


def _ttm_from_year_to_date(series: Durations, end: date) -> float | None:
    """TTM as last fiscal year plus the year to date less the same span a year earlier."""
    year_to_date = [
        (start, value)
        for (start, period_end), value in series.items()
        if period_end == end and _length(start, period_end) < ANNUAL_DAYS[0]
    ]
    if not year_to_date:
        return None
    # A 10-Q reports both the quarter and the year to date; the longer span is the latter.
    start, value = max(year_to_date, key=lambda item: _length(item[0], end))
    span = _length(start, end)
    prior_year = _annual_ending(series, start - timedelta(days=1), slack=7)
    prior_span = next(
        (
            prior_value
            for (prior_start, prior_end), prior_value in series.items()
            if _near(prior_end, _year_before(end))
            and abs(_length(prior_start, prior_end) - span) <= DATE_SLACK_DAYS
        ),
        None,
    )
    if prior_year is None or prior_span is None:
        return None
    return prior_year + value - prior_span


def _ttm_from_quarters(series: Durations, end: date) -> float | None:
    """TTM as the sum of four consecutive quarters ending at ``end``."""
    total = 0.0
    quarter_end = end
    for _ in range(4):
        quarter = next(
            (
                (start, value)
                for (start, period_end), value in series.items()
                if period_end == quarter_end
                and QUARTER_DAYS[0] <= _length(start, period_end) <= QUARTER_DAYS[1]
            ),
            None,
        )
        if quarter is None:
            return None
        total += quarter[1]
        quarter_end = quarter[0] - timedelta(days=1)
    return total


def trailing_twelve_months(series: Durations, end: date) -> float | None:
    """Return the trailing-twelve-month total of ``series`` ending at ``end``, if derivable.

    Uses, in order: a fiscal year ending at ``end``; the last fiscal year plus the year
    to date less the same span a year earlier; four consecutive quarters.
    """
    annual = _annual_ending(series, end)
    if annual is not None:
        return annual
    year_to_date = _ttm_from_year_to_date(series, end)
    if year_to_date is not None:
        return year_to_date
    return _ttm_from_quarters(series, end)


def _growth(current: float | None, previous: float | None) -> float | None:
    """Relative change from ``previous`` to ``current``; null unless ``previous`` is positive."""
    if current is None or previous is None or previous <= 0:
        return None
    return current / previous - 1.0


def _latest_ttm(series: Durations) -> tuple[date, float] | None:
    """The TTM total at the latest period end where one can be derived."""
    for end in sorted({end for _, end in series}, reverse=True):
        value = trailing_twelve_months(series, end)
        if value is not None:
            return end, value
    return None


def _fiscal_years(series: Durations, fallbacks: list[Durations]) -> list[AnnualValue]:
    """The last :data:`FISCAL_YEARS` fiscal years, oldest first, with year-on-year growth.

    Years missing from ``series`` are filled from ``fallbacks``, in order: they carry the
    same item under concepts the company used before switching.
    """
    years: dict[date, float] = {}
    for source in (series, *fallbacks):
        for (start, end), value in source.items():
            if _is_annual(start, end) and not any(_near(end, known, 7) for known in years):
                years[end] = value
    ends = sorted(years)
    history: list[AnnualValue] = []
    for previous, end in zip([None, *ends], ends):
        prior = years[previous] if previous and _near(previous, _year_before(end)) else None
        history.append(
            AnnualValue(fiscal_year_end=end, value=years[end], growth=_growth(years[end], prior))
        )
    return history[-FISCAL_YEARS:]


def flow_item(concept: str, series: Durations, fallbacks: list[Durations]) -> FlowItem | None:
    """Summarize ``series`` as a TTM total with growth and a fiscal-year history."""
    if not series:
        return None
    latest = _latest_ttm(series)
    ttm_end, ttm = latest if latest else (None, None)
    previous = None
    if ttm_end is not None:
        previous_end = next((end for _, end in series if _near(end, _year_before(ttm_end))), None)
        if previous_end is not None:
            previous = trailing_twelve_months(series, previous_end)
    return FlowItem(
        concept=concept,
        ttm=ttm,
        ttm_end=ttm_end,
        ttm_growth=_growth(ttm, previous),
        fiscal_years=_fiscal_years(series, fallbacks),
    )


def _latest_end(series: Durations | Instants) -> date:
    """The latest date ``series`` reaches."""
    return max(key[1] if isinstance(key, tuple) else key for key in series)


def _choose(candidates: dict[str, Any]) -> str | None:
    """The concept reported most recently, preferring earlier concepts on a tie.

    ``candidates`` maps concepts to their non-empty series, in order of preference.
    """
    if not candidates:
        return None
    order = list(candidates)
    return max(order, key=lambda concept: (_latest_end(candidates[concept]), -order.index(concept)))


@dataclass(frozen=True)
class ChosenFlow:
    """The concept chosen for a period item, its series, and the older concepts' series."""

    concept: str
    series: Durations
    fallbacks: list[Durations]


def _chosen_flow(company: dict[str, Any], concepts: tuple[str, ...]) -> ChosenFlow | None:
    """Pick the concept to use for a period item and gather its figures."""
    candidates = {
        concept: series
        for concept in concepts
        if (series := _durations(_rows(company, "us-gaap", concept, "USD")))
    }
    concept = _choose(candidates)
    if concept is None:
        return None
    fallbacks = [series for name, series in candidates.items() if name != concept]
    return ChosenFlow(concept, candidates[concept], fallbacks)


def _combine(
    first: ChosenFlow | None,
    second: ChosenFlow | None,
    operator: Callable[[float, float], float],
    symbol: str,
) -> FlowItem | None:
    """Derive an item from two others over the periods both report."""
    if first is None or second is None:
        return None
    series = {
        period: operator(value, second.series[period])
        for period, value in first.series.items()
        if period in second.series
    }
    return flow_item(f"{first.concept} {symbol} {second.concept}", series, [])


def _balance(instants: dict[str, Instants], as_of: date) -> BalanceItem | None:
    """The first concept with a value at ``as_of``, with its value a year earlier."""
    for concept, series in instants.items():
        if as_of in series:
            year_ago = next(
                (value for day, value in series.items() if _near(day, _year_before(as_of))),
                None,
            )
            return BalanceItem(concept=concept, value=series[as_of], as_of=as_of, year_ago=year_ago)
    return None


def _debt_at(company: dict[str, Any], as_of: date) -> BalanceItem | None:
    """Total debt at ``as_of``: long-term debt, including its current part, and borrowings.

    Returns ``None`` when no long-term debt concept has a value at ``as_of``: the company
    may report its debt under concepts not recognized here, so zero would be a guess.
    """

    def value(concept: str) -> float | None:
        return _instants(_rows(company, "us-gaap", concept, "USD")).get(as_of)

    def total(concepts: Iterable[str]) -> tuple[list[str], float]:
        found = [(concept, value(concept)) for concept in concepts]
        present = [(concept, amount) for concept, amount in found if amount is not None]
        return [concept for concept, _ in present], sum(amount for _, amount in present)

    noncurrent = next(
        (
            (concept, amount)
            for concept in LONG_TERM_DEBT_NONCURRENT
            if (amount := value(concept)) is not None
        ),
        None,
    )
    if noncurrent is not None:
        current = value(DEBT_CURRENT)
        if current is not None:
            parts, amount = [noncurrent[0], DEBT_CURRENT], noncurrent[1] + current
        else:
            names, current_total = total(CURRENT_DEBT_PARTS)
            parts, amount = [noncurrent[0], *names], noncurrent[1] + current_total
    elif (whole := value(LONG_TERM_DEBT_TOTAL)) is not None:
        names, borrowed = total(SHORT_TERM_BORROWINGS)
        parts, amount = [LONG_TERM_DEBT_TOTAL, *names], whole + borrowed
    else:
        return None
    return BalanceItem(concept=" + ".join(parts), value=amount, as_of=as_of)


def _shares(company: dict[str, Any]) -> BalanceItem | None:
    """Shares outstanding: the more recent of the cover-page and balance sheet counts.

    A cover page lists each share class separately when the company has several, so a
    cover-page date with more than one count is skipped.
    """
    cover: dict[date, set[float]] = {}
    for fact in _rows(company, "dei", COVER_SHARES, "shares"):
        if fact.start is None:
            cover.setdefault(fact.end, set()).add(fact.value)
    sources = {
        COVER_SHARES: {day: values.pop() for day, values in cover.items() if len(values) == 1},
        BALANCE_SHARES: _instants(_rows(company, "us-gaap", BALANCE_SHARES, "shares")),
    }
    latest = [(max(series), concept) for concept, series in sources.items() if series]
    if not latest:
        return None
    day, concept = max(latest)
    return BalanceItem(concept=concept, value=sources[concept][day], as_of=day)


def _last_date(item: FlowItem) -> date | None:
    """The end of the latest period ``item`` has a figure for."""
    ends = [annual.fiscal_year_end for annual in item.fiscal_years]
    if item.ttm_end is not None:
        ends.append(item.ttm_end)
    return max(ends, default=None)


def normalize(company: dict[str, Any], cik: int) -> Financials:
    """Turn a company facts document into :class:`Financials`.

    Raises:
        FinancialsNotFoundError: If the document has none of the items read here.
    """
    flows = {name: _chosen_flow(company, concepts) for name, concepts in FLOW_CONCEPTS.items()}
    items: dict[str, FlowItem | None] = {
        name: flow_item(chosen.concept, chosen.series, chosen.fallbacks) if chosen else None
        for name, chosen in flows.items()
    }
    items["free_cash_flow"] = _combine(
        flows["operating_cash_flow"], flows["capital_expenditure"], lambda a, b: a - b, "-"
    )
    items["ebitda"] = _combine(
        flows["operating_income"], flows["depreciation_amortization"], lambda a, b: a + b, "+"
    )

    balances = {
        name: {
            concept: series
            for concept in concepts
            if (series := _instants(_rows(company, "us-gaap", concept, "USD")))
        }
        for name, concepts in BALANCE_CONCEPTS.items()
    }
    equity = balances["stockholders_equity"]
    balance_date = max((_latest_end(series) for series in equity.values()), default=None)
    cash = equity_item = debt = None
    if balance_date is not None:
        cash = _balance(balances["cash"], balance_date)
        equity_item = _balance(equity, balance_date)
        debt = _debt_at(company, balance_date)

    reached = [day for item in items.values() if item and (day := _last_date(item))]
    if balance_date is not None:
        reached.append(balance_date)
    if not reached:
        raise FinancialsNotFoundError(
            "SEC EDGAR has no US GAAP financial statements for this company."
        )
    latest = max(reached)
    cutoff = latest - timedelta(days=STALE_AFTER_DAYS)
    current = {
        name: item if item and (day := _last_date(item)) and day >= cutoff else None
        for name, item in items.items()
    }
    shares = _shares(company)
    return Financials(
        cik=cik,
        entity_name=str(company.get("entityName") or ""),
        latest_period_end=latest,
        cash=cash,
        total_debt=debt,
        stockholders_equity=equity_item,
        shares_outstanding=shares if shares and shares.as_of >= cutoff else None,
        **current,
    )


_memory: "OrderedDict[int, tuple[float, Financials]]" = OrderedDict()


def clear_memory() -> None:
    """Forget the financials kept in memory."""
    _memory.clear()


def _parse(content: bytes, cik: int) -> Financials:
    """Decode and normalize a company facts document (blocking)."""
    company: Any = json.loads(content)
    if not isinstance(company, dict):
        raise ValueError("The company facts document is not a JSON object.")
    return normalize(company, cik)


def _remember(cik: int, financials: Financials) -> None:
    """Keep ``financials`` in memory, dropping the least recently used beyond the limit."""
    _memory[cik] = (time.monotonic(), financials)
    _memory.move_to_end(cik)
    while len(_memory) > MEMORY_CACHE_SIZE:
        _memory.popitem(last=False)


async def _cached(cik: int) -> Financials | None:
    """Return financials normalized within :data:`CACHE_TTL_SECONDS`, from memory or Redis."""
    entry = _memory.get(cik)
    if entry is not None and time.monotonic() - entry[0] <= CACHE_TTL_SECONDS:
        _memory.move_to_end(cik)
        return entry[1]
    text = await cache_get(f"fundamentals:{cik}")
    if text is None:
        return None
    try:
        financials = Financials.model_validate_json(text)
    except ValidationError:
        logger.warning("Ignoring unreadable cached financials for CIK %d.", cik)
        return None
    _remember(cik, financials)
    return financials


async def _fetch(symbol: str, client: httpx2.AsyncClient) -> Financials:
    """Fetch and normalize the financials of ``symbol`` with ``client``."""
    cik = await cik_for(symbol, client)
    cached = await _cached(cik)
    if cached is not None:
        return cached
    try:
        response = await edgar_get(client, COMPANY_FACTS_URL.format(cik=cik))
    except httpx2.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise FinancialsNotFoundError(
                f"SEC EDGAR has no financial data for {symbol!r}."
            ) from exc
        raise
    financials = await asyncio.to_thread(_parse, response.content, cik)
    _remember(cik, financials)
    await cache_set(f"fundamentals:{cik}", financials.model_dump_json(), CACHE_TTL_SECONDS)
    return financials


async def fetch_financials(symbol: str, client: httpx2.AsyncClient | None = None) -> Financials:
    """Fetch ``symbol``'s financial statement figures from SEC EDGAR.

    Args:
        symbol: Ticker symbol, e.g. ``"AAPL"``. Case and surrounding whitespace are ignored.
        client: Optional HTTP client, which must send the SEC User-Agent itself. A
            short-lived client is created when omitted.

    Returns:
        TTM totals, fiscal-year histories and the latest balance sheet, as far as the
        company reports them.

    Raises:
        EdgarNotConfiguredError: If ``SEC_USER_AGENT`` is not set.
        CompanyNotFoundError: If no SEC registrant has ``symbol`` as a ticker.
        FinancialsNotFoundError: If the company has no US GAAP financial data.
        FilingFetchError: If EDGAR cannot be reached or returns an unexpected response.
    """
    symbol = symbol.strip().upper()
    with edgar_errors(f"SEC financial data for {symbol!r}"):
        if client is not None:
            return await _fetch(symbol, client)
        async with edgar_client() as own_client:
            return await _fetch(symbol, own_client)
