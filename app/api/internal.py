"""Routes Google Cloud calls, not people: left out of the API schema, and refused unless
the caller proves it is the expected service account.

``POST /internal/nightly-refresh`` is called by Cloud Scheduler every hour. It starts the
nightly refresh job only when some user's refresh is due, so the job does not start every
hour to find nothing to do (see :mod:`app.core.cloud_run`).
"""

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from app.core.cloud_run import JobStartError, run_job, verify_caller
from app.core.config import get_settings
from app.db.nightly import any_due
from app.db.session import get_sessionmaker
from app.models.nightly import NightlyTriggerResponse

logger = logging.getLogger(__name__)

NIGHTLY_TRIGGER_AUDIENCE: str = "pyqxel-nightly-trigger"
"""The audience Cloud Scheduler's identity token names (``--oidc-token-audience`` in
``deploy/cloud-run.sh``), so a token made for anything else is refused."""

router = APIRouter(prefix="/internal", include_in_schema=False)


@router.post("/nightly-refresh", response_model=NightlyTriggerResponse)
async def start_nightly_refresh(
    authorization: Annotated[str | None, Header()] = None,
) -> NightlyTriggerResponse:
    """Start the nightly refresh job if any user's refresh is due.

    Answers 404 when the server has no nightly job configured, 401 unless the call carries
    Cloud Scheduler's identity token, 503 when the database cannot be read, and 502 when
    Cloud Run does not start the job.
    """
    settings = get_settings()
    if settings.nightly_job is None or settings.nightly_trigger_caller is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not await verify_caller(
        token, NIGHTLY_TRIGGER_AUDIENCE, settings.nightly_trigger_caller
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not a recognized caller.")
    try:
        async with get_sessionmaker()() as session:
            due = await any_due(session, datetime.now(timezone.utc))
    except SQLAlchemyError as exc:
        logger.warning("The nightly check could not read the database: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The database cannot be read."
        ) from exc
    if not due:
        return NightlyTriggerResponse(started=False)
    try:
        await run_job(settings.nightly_job)
    except JobStartError as exc:
        logger.warning("The nightly refresh job was not started: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The job was not started.") from exc
    logger.info("A nightly refresh is due, so the job was started.")
    return NightlyTriggerResponse(started=True)
