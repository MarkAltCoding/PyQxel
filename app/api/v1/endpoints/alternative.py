"""Alternative data routes: a company's lobbying, government contracts, patents and SEC
filings from Finnhub, on the user's own key."""

from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, status

from app.api.v1.endpoints.stocks import Symbol
from app.data.alternative import fetch_alternative
from app.data.fetcher import ProviderNotConfiguredError
from app.models.alternative import AlternativeData
from app.stats.alternative import summarize_alternative

router = APIRouter()


@router.get(
    "/{symbol}/alternative",
    response_model=AlternativeData,
    summary="Lobbying, government contracts, patents and SEC filings",
)
async def get_alternative(symbol: Symbol) -> AlternativeData:
    """Return ``symbol``'s lobbying, federal contracts, patents and recent SEC filings.

    ``lobbying_years`` totals last year's and this year's quarterly disclosures under the Lobbying
    Disclosure Act, whether the company lobbied itself or hired firms, the newest of
    which are in ``lobbying``. ``contracts_total`` sums the last year's federal awards
    from USAspending.gov to the company and its subsidiaries, ``contract_agencies`` says
    who awarded them, and ``contracts`` lists the largest. ``patents_count`` counts the
    patents and applications the USPTO published in the last year, the newest of which
    are in ``patents``. ``filing_counts`` counts the last 90 days of SEC filings by form,
    and ``filings`` lists the newest.

    Fetched on your own Finnhub key (``X-Finnhub-Key``), whose free plan
    covers US companies, and reused for a day. A part Finnhub cannot supply is left
    empty and named in ``notice``. Returns 403 without a valid Finnhub key.
    """
    try:
        records = await fetch_alternative(symbol, date.today())
    except ProviderNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return summarize_alternative(records, datetime.now(timezone.utc))
