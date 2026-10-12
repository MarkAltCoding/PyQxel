"""Stock search by symbol or company name, over the screened universe."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.screener import search_stocks
from app.db.session import get_session
from app.models.screener import SearchResponse

logger = logging.getLogger(__name__)

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=SearchResponse, summary="Search stocks")
async def search(
    session: Session,
    q: Annotated[str, Query(min_length=1, max_length=100, description="Symbol or name.")],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SearchResponse:
    """Find US-listed common stocks by symbol prefix or part of the company name.

    Searches the screened universe, which the screener refresh job builds, so it reads
    the database only and needs no data provider. Stocks outside the universe, such as
    ETFs and foreign listings, are not found here but work on every ``/stocks/{symbol}``
    route.
    """
    try:
        items = await search_stocks(session, q, limit)
    except SQLAlchemyError as exc:
        logger.error("Search failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The search database is unavailable.",
        ) from exc
    return SearchResponse(query=q, items=items)
