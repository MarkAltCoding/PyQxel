"""Signals routes: analyst ratings, earnings results and dates, and insider trades from
Finnhub, on the user's own key."""

from datetime import date

from fastapi import APIRouter, HTTPException, status

from app.api.v1.endpoints.stocks import Symbol
from app.data.fetcher import ProviderNotConfiguredError
from app.data.finnhub import fetch_signals
from app.models.signals import MarketSignals
from app.stats.signals import summarize_signals

router = APIRouter()


@router.get(
    "/{symbol}/signals",
    response_model=MarketSignals,
    summary="Analyst ratings, earnings and insider trades",
)
async def get_signals(symbol: Symbol) -> MarketSignals:
    """Return what analysts, earnings and insiders say about ``symbol``, from Finnhub.

    ``recommendations`` counts analysts' ratings by month, ``earnings`` compares the last
    quarters' reported EPS with the estimate, ``next_earnings`` gives the next report
    within three months, ``insiders`` totals the last 90 days of open-market purchases
    and sales filed on Form 4, the newest of which are listed in ``insider_trades``, and
    ``insider_sentiment`` gives Finnhub's monthly share purchase ratio for the last year.

    Fetched on your own Finnhub key (``PUT /api/v1/me/credentials``), whose free plan
    covers US companies, and reused for six hours. A part Finnhub cannot supply is left
    empty and named in ``notice``. Returns 403 without a valid Finnhub key.
    """
    try:
        signals = await fetch_signals(symbol, date.today())
    except ProviderNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return summarize_signals(signals)
