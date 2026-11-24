"""Stock search by symbol or company name, over the screened universe, Finnhub's US
listings with a Finnhub key, and the SEC's lists of listed companies and of funds."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.fetcher import DataFetchError, finnhub_key
from app.data.finnhub import search_symbols
from app.data.fund_filings import search_fund_tickers
from app.data.sec_edgar import FilingFetchError, search_tickers
from app.db.screener import search_stocks, shows_universe
from app.db.session import get_session
from app.models.screener import SearchMatch, SearchResponse

logger = logging.getLogger(__name__)

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]


async def _finnhub_matches(query: str, limit: int) -> list[SearchMatch]:
    """Finnhub's matches, or none without a key or when Finnhub fails."""
    if finnhub_key() is None:
        return []
    try:
        return await search_symbols(query, limit)
    except DataFetchError as exc:
        logger.info("Finnhub search for %r failed: %s", query, exc)
        return []


async def _sec_matches(query: str, limit: int) -> list[SearchMatch]:
    """Matches on the SEC's lists of listed companies and of fund tickers, which need no
    key: an exact ticker from either first, then companies, then funds; none from a list
    that cannot be read."""
    try:
        companies = await search_tickers(query, limit)
    except FilingFetchError as exc:
        logger.info("SEC ticker search for %r failed: %s", query, exc)
        companies = []
    try:
        funds = await search_fund_tickers(query, limit)
    except FilingFetchError as exc:
        logger.info("SEC fund ticker search for %r failed: %s", query, exc)
        funds = []
    wanted = query.strip().upper()
    seen: set[str] = set()
    matches: list[SearchMatch] = []
    exact = [m for m in [*companies, *funds] if m[0] == wanted]
    for ticker, name in [*exact, *companies, *funds]:
        if ticker in seen:
            continue
        seen.add(ticker)
        # EDGAR writes share classes with a dash (BRK-B), as the app's routes take them.
        matches.append(SearchMatch(symbol=ticker, name=name, source="sec"))
    return matches[:limit]


@router.get("", response_model=SearchResponse, summary="Search stocks")
async def search(
    session: Session,
    q: Annotated[str, Query(min_length=1, max_length=100, description="Symbol or name.")],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SearchResponse:
    """Find US listings by symbol prefix or part of the company name.

    A listing whose ticker is exactly ``q`` comes first. Searches the screened universe of
    US common stocks, which the screener refresh job builds, first. With a Finnhub key,
    Finnhub's search of US listings fills the rest of the ``limit``, or adds the exact
    ticker when the universe lacks it, finding ETFs, ADRs, and stocks outside the universe
    (``source`` is ``finnhub``, with their ``kind`` and no market cap). The SEC's list of listed
    companies, which needs no key, fills any room left (``source`` ``sec``), so typing
    the start of a ticker suggests it even when the universe is empty and there is no
    Finnhub key. Every listing works on the
    ``/stocks/{symbol}`` routes whether it is found here or not.

    Returns 503 when the database is unavailable and Finnhub finds nothing.
    """
    try:
        items = await search_stocks(session, q, limit, details=await shows_universe(session))
    except SQLAlchemyError as exc:
        logger.error("Search failed: %s", exc)
        items = None
    wanted = q.strip().upper()
    exact = any(item.symbol == wanted for item in items or [])
    if items is None or len(items) < limit or not exact:
        # An ETF or a listing abroad is not in the universe of US stocks, so Finnhub is
        # asked when its exact ticker was not found there, as for SPY among names with
        # "Spy" in them.
        known = {item.symbol for item in items or []}
        extra = [m for m in await _finnhub_matches(q, limit) if m.symbol not in known]
        # The SEC's lists fill any room left, and are asked for the exact ticker even when
        # there is none, as for VO among the many tickers starting with it.
        found_exact = any(m.symbol == wanted for m in extra)
        if len(known) + len(extra) < limit or not found_exact:
            known |= {m.symbol for m in extra}
            extra += [m for m in await _sec_matches(q, limit) if m.symbol not in known]
        if items is None and not extra:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The search database is unavailable.",
            )
        items = [*(items or []), *extra]
    # The listing whose ticker was typed comes first.
    items = sorted(items, key=lambda item: item.symbol != wanted)[:limit]
    return SearchResponse(query=q, items=items)
