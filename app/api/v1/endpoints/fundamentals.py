"""Fundamentals routes: financial statement figures from SEC filings, and valuation."""

import asyncio
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, status

from app.api.v1.endpoints.stocks import Symbol
from app.data.exchange_rates import fetch_exchange_rate
from app.data.fetcher import DataFetchError, SymbolNotFoundError, fetch_ticker_info
from app.data.fundamentals import FinancialsNotFoundError, fetch_financials
from app.data.sec_edgar import CompanyNotFoundError, EdgarNotConfiguredError, FilingFetchError
from app.models.fundamentals import Financials, Fundamentals, FundamentalsResponse
from app.models.stock import TickerInfo
from app.stats.valuation import value_company

router = APIRouter()

STALE_FINANCIALS_DAYS: int = 200
"""Age of the latest period, in days, after which the financials are flagged as stale.
Annual reports are due up to 90 days after year end, so a current filer is never this far
behind."""

STALE_ANNUAL_FINANCIALS_DAYS: int = 500
"""The same for IFRS filers, which are foreign issuers filing only an annual report with
EDGAR, due four months after the fiscal year it covers."""

DEFAULT_LISTING_CURRENCY: str = "USD"
"""Currency assumed for a listing whose provider does not name one."""


def edgar_http_error(exc: FilingFetchError) -> HTTPException:
    """Translate an EDGAR failure into an HTTP error: 503, 404 or 502."""
    if isinstance(exc, EdgarNotConfiguredError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, (CompanyNotFoundError, FinancialsNotFoundError)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


def financials_failure_notice(symbol: str, exc: FilingFetchError) -> str:
    """Explain, for a report written without them, why the financials are missing."""
    if isinstance(exc, EdgarNotConfiguredError):
        return "SEC financial data is not configured, so fundamentals were left out."
    if isinstance(exc, (CompanyNotFoundError, FinancialsNotFoundError)):
        return f"{symbol} has no US GAAP or IFRS financial statements on SEC EDGAR."
    return "SEC financial data could not be fetched, so fundamentals were left out."


def _freshness_notices(financials: Financials, today: date) -> list[str]:
    """Flag financials that are old, and items that end before the latest period."""
    notices: list[str] = []
    latest = financials.latest_period_end
    stale_after = (
        STALE_ANNUAL_FINANCIALS_DAYS
        if financials.accounting_standard == "IFRS"
        else STALE_FINANCIALS_DAYS
    )
    if latest is not None and today - latest > timedelta(days=stale_after):
        notices.append(
            f"The latest financial statements cover the period ending {latest}, so the "
            "fundamentals may be out of date."
        )
    lagging = [
        name
        for name, item in (
            ("revenue", financials.revenue),
            ("net income", financials.net_income),
            ("free cash flow", financials.free_cash_flow),
            ("EBITDA", financials.ebitda),
        )
        if item is not None and item.ttm_end is not None and latest and item.ttm_end < latest
    ]
    if lagging:
        notices.append(f"TTM {', '.join(lagging)} end before {latest}, the latest period reported.")
    return notices


def listing_currency(info: TickerInfo) -> str:
    """The currency ``info``'s price and market cap are in."""
    return (info.currency or DEFAULT_LISTING_CURRENCY).upper()


async def statement_exchange_rate(financials: Financials, info: TickerInfo | None) -> float | None:
    """Listing currency per unit of the statements' currency, when they differ and a rate
    is available; ``None`` otherwise."""
    if info is None or financials.currency.upper() == listing_currency(info):
        return None
    try:
        return await fetch_exchange_rate(financials.currency, listing_currency(info))
    except DataFetchError:
        return None


def build_fundamentals(
    financials: Financials,
    info: TickerInfo | None,
    exchange_rate: float | None = None,
    today: date | None = None,
) -> tuple[Fundamentals, list[str]]:
    """Value the company from ``info`` and say what is missing or stale.

    The valuation is left out when there is no market value. Statements in a currency
    other than the listing's are converted at ``exchange_rate``; see
    :func:`~app.stats.valuation.value_company`.
    """
    notices = _freshness_notices(financials, today or date.today())
    valuation = None
    if info is None:
        notices.append("No market value is available, so valuation multiples were left out.")
    else:
        valuation = value_company(
            financials, info.price, info.market_cap, listing_currency(info), exchange_rate
        )
        if valuation is None:
            notices.append(
                "Neither a market cap nor a price and share count is available, so "
                "valuation multiples were left out."
            )
    return Fundamentals(financials=financials, valuation=valuation), notices


@router.get(
    "/{symbol}/fundamentals",
    response_model=FundamentalsResponse,
    summary="Financial statements from SEC filings, with valuation multiples",
)
async def get_fundamentals(symbol: Symbol) -> FundamentalsResponse:
    """Return ``symbol``'s financial statement figures and valuation.

    Figures come from the company's XBRL filings on SEC EDGAR, under US GAAP or IFRS and
    in the currency it reports in (``financials.currency``): trailing-twelve-month (TTM)
    revenue, profits, cash flows and EBITDA with year-on-year
    growth, five fiscal years of history, and the latest balance sheet. They are combined
    with the current market cap into P/E, P/S, EV/EBITDA, free-cash-flow yield, margins
    and return on equity, converting statements in another currency at the latest
    exchange rate. Growth rates, margins, yields and returns are decimals.

    Items a company does not report under a recognized concept are null, as are ratios
    that are not meaningful; ``valuation.notes`` and ``notice`` say why.

    Returns 404 when the symbol has no SEC registrant or no US GAAP or IFRS data, 502
    when EDGAR fails, and 503 when ``SEC_USER_AGENT`` is not set.
    """
    financials_result, info_result = await asyncio.gather(
        fetch_financials(symbol), fetch_ticker_info(symbol), return_exceptions=True
    )
    if isinstance(financials_result, FilingFetchError):
        raise edgar_http_error(financials_result) from financials_result
    if isinstance(financials_result, BaseException):
        raise financials_result
    symbol = symbol.upper()

    notices: list[str] = []
    info: TickerInfo | None = None
    if isinstance(info_result, (SymbolNotFoundError, DataFetchError)):
        notices.append(f"Market data for {symbol} could not be fetched: {info_result}")
    elif isinstance(info_result, BaseException):
        raise info_result
    else:
        info = info_result

    rate = await statement_exchange_rate(financials_result, info)
    fundamentals, fundamentals_notices = build_fundamentals(financials_result, info, rate)
    notices.extend(fundamentals_notices)
    return FundamentalsResponse(
        symbol=symbol,
        financials=fundamentals.financials,
        valuation=fundamentals.valuation,
        notice=" ".join(notices) or None,
    )
