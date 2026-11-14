"""What an account enters in the app, kept on the server so it follows the account.

Holdings, backtests, reports, reviews, and picks have tables of their own. This keeps the
rest of what a person types or chooses that is not a secret (strategy drafts, a screen's
filters, the answers given for a review, display choices), which once lived only in one
browser and was lost with it. Provider keys and sign-in keys are never accepted here.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import CurrentUser
from app.db.saved_state import SavedStateFullError, load_saved_state, update_saved_state
from app.db.session import get_session
from app.models.saved_state import SavedState, SavedStateUpdate

logger = logging.getLogger(__name__)

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]


def _database_error(exc: SQLAlchemyError) -> HTTPException:
    """Log a database failure and translate it into a 503."""
    logger.error("Saved state database request failed: %s", exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Your saved settings cannot be reached while the database is unavailable.",
    )


@router.get("", response_model=SavedState, summary="What you have entered in the app")
async def read_saved_state(session: Session, user: CurrentUser) -> SavedState:
    """Return everything the app has saved for your account, by name: strategy drafts,
    screens, the answers you gave for reviews and picks, and display choices. Returns 503
    when the database is unavailable."""
    try:
        return SavedState(account=user.id, items=await load_saved_state(session, user.id))
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc


@router.put("", response_model=SavedState, summary="Save what you entered in the app")
async def store_saved_state(
    session: Session, user: CurrentUser, update: Annotated[SavedStateUpdate, Body()]
) -> SavedState:
    """Save each named value, replacing what was there, and forget those sent as null;
    names not sent are left as they are. Returns everything then saved.

    Returns 413 when the account would hold more than it may, 422 for a name that is not
    allowed, such as one naming a key or sign-in, which are never saved to the account,
    and 503 when the database is unavailable.
    """
    try:
        await update_saved_state(session, user.id, dict(update.items))
        return SavedState(account=user.id, items=await load_saved_state(session, user.id))
    except SavedStateFullError as exc:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise _database_error(exc) from exc
