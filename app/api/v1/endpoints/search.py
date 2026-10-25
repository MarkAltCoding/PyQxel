"""Stock search by symbol or company name, over the screened universe and, with a Finnhub
key, Finnhub's US listings."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.fetcher import DataFetchError, finnhub_key
from app.data.finnhub import search_symbols
from app.db.screener import search_stocks
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


@router.get("", response_model=SearchResponse, summary="Search stocks")
async def search(
    session: Session,
    q: Annotated[str, Query(min_length=1, max_length=100, description="Symbol or name.")],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SearchResponse:
    """Find US listings by symbol prefix or part of the company name.

    Searches the screened universe of US common stocks, which the screener refresh job
    builds, first. With a Finnhub key, Finnhub's search of US listings fills the rest of
    the ``limit``, finding ETFs, ADRs and stocks outside the universe (``source`` is
    ``finnhub``, with their ``kind`` and no market cap). Every listing works on the
    ``/stocks/{symbol}`` routes whether it is found here or not.

    Returns 503 when the database is unavailable and Finnhub finds nothing.
    """
    try:
        items = await search_stocks(session, q, limit)
    except SQLAlchemyError as exc:
        logger.error("Search failed: %s", exc)
        items = None
    if items is None or len(items) < limit:
        known = {item.symbol for item in items or []}
        extra = [m for m in await _finnhub_matches(q, limit) if m.symbol not in known]
        if items is None and not extra:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The search database is unavailable.",
            )
        items = [*(items or []), *extra][:limit]
    return SearchResponse(query=q, items=items)
