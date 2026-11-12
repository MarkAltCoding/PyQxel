"""Stock screener routes over the precomputed universe.

The universe and its metrics are built by the refresh job (``python -m app.jobs.screener``),
so screening reads the database only and answers in milliseconds. A user who opted in to
the nightly refresh screens their own universe, built from their keys, once it has been
built; everyone else screens the shared one.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import CurrentUser
from app.data.fetcher import DataFetchError
from app.data.index_members import cached_index_members
from app.db.screener import (
    Universe,
    load_listings,
    screen,
    shared_universe,
    shows_universe,
    universe_listings,
    universe_status,
    user_universe,
)
from app.db.session import get_session
from app.models.screener import ScreenRequest, ScreenResponse, UniverseStatus

logger = logging.getLogger(__name__)

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]


def _database_error(exc: SQLAlchemyError) -> HTTPException:
    """Log a database failure and translate it into a 503."""
    logger.error("Screener database request failed: %s", exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="The screener database is unavailable.",
    )


async def _universe(session: AsyncSession, user_id: str) -> Universe | None:
    """The user's own universe, or ``None`` to use the shared one."""
    return await user_universe(session, user_id)


@router.post("", response_model=ScreenResponse, summary="Screen stocks by their metrics")
async def screen_stocks(
    user: CurrentUser,
    session: Session,
    request: Annotated[ScreenRequest, Body()] = ScreenRequest(),
) -> ScreenResponse:
    """Return the stocks matching ``request``'s filters, sorted and paged.

    ``universe`` narrows the screen to ``sp500`` (the S&P 500's companies, from the SEC
    filings of IVV, a fund that tracks it), ``nasdaq`` (stocks and ETFs listed on Nasdaq),
    ``large_cap`` (the 500 largest stocks), ``broad_market`` (the 3,000 largest, like the
    Russell 3000) or ``liquid`` ($5M or more traded a day), or keeps ``all`` stocks that
    passed the baseline filter. Each filter keeps stocks whose ``field`` is within ``min`` and
    ``max`` and drops those without a value. Results are sorted by ``sort.field``, stocks
    without it last; ``rank`` is each stock's place in that order.

    Rank by factor exposure by sorting on a beta: ``{"field": "beta_market",
    "descending": false}`` lists the lowest market betas first.

    The user's own universe is screened when their nightly refresh has built one
    (``own`` is then true), and the shared one otherwise.

    Returns 503 when the universe has not been built yet, the database is unavailable, or
    the S&P 500's companies cannot be read from the SEC just then.
    """
    try:
        universe = await _universe(session, user.id)
        shown = universe is not None or await shows_universe(session)
        classes = None
        if shown and request.universe == "sp500":
            listings = [listing for listing, _ in await load_listings(session)]
            if not listings:
                listings = await universe_listings(
                    session, universe or await shared_universe(session)
                )
            classes = (await cached_index_members("sp500", listings)).classes
        result = await screen(session, request, universe, classes) if shown else None
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
    except DataFetchError as exc:
        logger.warning("The S&P 500's companies could not be read: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The S&P 500's list of companies could not be read from the SEC just now. "
            "Try again in a minute, or choose “Large cap (top 500 stocks)” meanwhile.",
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The stock screener is not available yet.",
        )
    if result.refreshed_at is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The screened universe has not been built yet; run "
            "`python -m app.jobs.screener`.",
        )
    return result


@router.get("/status", response_model=UniverseStatus, summary="State of the screened universe")
async def read_status(user: CurrentUser, session: Session) -> UniverseStatus:
    """Return how many stocks the universe holds, when it was refreshed, and any failure.

    It describes the user's own universe when their nightly refresh has built one.

    Returns 503 when the database is unavailable.
    """
    try:
        universe = await _universe(session, user.id)
        if universe is None and not await shows_universe(session):
            return UniverseStatus(stocks=0)
        return await universe_status(session, universe)
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
