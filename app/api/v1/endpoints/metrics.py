"""Metrics routes: a company's key metrics and peers from Finnhub, on the user's own key."""

from fastapi import APIRouter

from app.api.v1.endpoints.stocks import Symbol, upstream_error
from app.data.fetcher import DataFetchError
from app.data.finnhub import fetch_company_metrics
from app.models.metrics import CompanyMetrics

router = APIRouter()


@router.get("/{symbol}/metrics", response_model=CompanyMetrics, summary="Key metrics and peers")
async def get_metrics(symbol: Symbol) -> CompanyMetrics:
    """Return ``symbol``'s key metrics and peers, from Finnhub's basic financials.

    The metrics cover the 52-week range and returns, beta and trading volume; valuation
    multiples, including a forward P/E on analysts' estimates; the dividend yield, payout
    and growth; margins and returns on capital; growth; and liquidity and leverage, as
    Finnhub computes them from its own data, so they can differ slightly from the
    fundamentals built from SEC filings. Ratios are decimals and totals are in units.
    ``peers`` lists companies Finnhub places in the same industry.

    Fetched on your own Finnhub key (``PUT /api/v1/me/credentials``), whose free plan
    covers US listings, and reused for six hours. Returns 403 without a valid Finnhub key,
    402 when your plan does not cover the company, 404 when Finnhub has no metrics for it,
    and 502 when Finnhub fails.
    """
    try:
        return await fetch_company_metrics(symbol)
    except DataFetchError as exc:
        raise upstream_error(exc) from exc
