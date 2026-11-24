"""Metrics routes: a company's key metrics and peers from Finnhub, on the user's own key."""

import asyncio
import logging

from fastapi import APIRouter

from app.core.clock import market_today
from app.api.v1.endpoints.stocks import Symbol, upstream_error
from app.data.fetcher import DataFetchError, fetch_price_history
from app.data.finnhub import fetch_company_metrics
from app.data.fundamentals import fetch_financials
from app.data.sec_edgar import FilingFetchError
from app.models.fundamentals import Financials
from app.models.metrics import CompanyMetrics
from app.stats.performance import price_range
from app.stats.valuation import with_filed_figures

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{symbol}/metrics", response_model=CompanyMetrics, summary="Key metrics and peers")
async def get_metrics(symbol: Symbol) -> CompanyMetrics:
    """Return ``symbol``'s key metrics and peers, from Finnhub's basic financials.

    The metrics cover the 52-week range and returns, beta, and trading volume; valuation
    multiples, including a forward P/E on analysts' estimates; the dividend yield, payout
    and growth; margins and returns on capital; growth; and liquidity and leverage, as
    Finnhub computes them from its own data, so they can differ slightly from the
    fundamentals built from SEC filings. Revenue growth is the filed one where the SEC's
    financials are current, and price to free cash flow is left out for a company that
    files no capital expenditure, as a bank does, since Finnhub's are wrong for banks
    (see :func:`~app.stats.valuation.with_filed_figures`). Ratios are decimals and totals
    are in units.
    ``peers`` lists companies Finnhub places in the same industry. For a foreign
    company's US listing, which Finnhub describes by its home listing abroad, the 52-week
    range is worked out from the US listing's own daily prices instead, without its dates.

    Fetched on your own Finnhub key (``X-Finnhub-Key``), whose free plan
    covers US listings, and reused for six hours. Returns 403 without a valid Finnhub key,
    402 when your plan does not cover the company, 404 when Finnhub has no metrics for it,
    and 502 when Finnhub fails.
    """
    found_metrics, financials = await asyncio.gather(
        fetch_company_metrics(symbol), fetch_financials(symbol), return_exceptions=True
    )
    if isinstance(found_metrics, DataFetchError):
        raise upstream_error(found_metrics) from found_metrics
    if isinstance(found_metrics, BaseException):
        raise found_metrics
    filed: Financials | None = None
    if isinstance(financials, FilingFetchError | DataFetchError):
        logger.info("No filed figures for %s's metrics: %s", symbol, financials)
    elif isinstance(financials, BaseException):
        raise financials
    else:
        filed = financials
    metrics = with_filed_figures(found_metrics.metrics, filed, market_today())
    result = found_metrics.model_copy(update={"metrics": metrics})
    if metrics.week_52_high is None or metrics.week_52_low is None:
        try:
            found = price_range(await fetch_price_history(symbol, "1y"))
        except DataFetchError as exc:
            logger.info("No 52-week range for %s from its prices: %s", symbol, exc)
            found = None
        if found is not None:
            metrics = metrics.model_copy(
                update={"week_52_high": found.high, "week_52_low": found.low}
            )
            result = result.model_copy(update={"metrics": metrics})
    return result
