"""Health checks for containers, load balancers and uptime monitors; no API key needed.

``/health`` is a liveness check: it answers whenever the process does, so a database
outage does not get every app instance restarted. ``/health/ready`` checks the database
and Redis and answers 503 when the database cannot serve, so traffic goes elsewhere until
it recovers. Neither reveals error details.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Response, status

from app.core.cache import check_cache
from app.core.config import get_settings
from app.db.session import check_database
from app.models.health import HealthResponse, ReadinessChecks, ReadinessResponse
from app.stats.r_bridge import r_available
from app.version import __version__

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness")
async def health() -> HealthResponse:
    """Report that the service is up."""
    return HealthResponse(status="ok", app=get_settings().app_name, version=__version__)


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness",
    responses={503: {"model": ReadinessResponse, "description": "The database cannot serve."}},
)
async def ready(response: Response) -> ReadinessResponse:
    """Check the database and Redis; 503 when the database is down or not migrated.

    Redis is optional, so its outage only marks the service ``degraded``. R is reported
    for information: the image without R serves everything but GARCH.
    """
    database, redis = await asyncio.gather(check_database(), check_cache())
    started = r_available()
    checks = ReadinessChecks(
        database=database,
        redis=redis,
        r="not_started" if started is None else "ok" if started else "unavailable",
    )
    overall: Literal["ok", "degraded", "unavailable"]
    if database != "ok":
        overall = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif redis == "unavailable":
        overall = "degraded"
    else:
        overall = "ok"
    return ReadinessResponse(
        status=overall, app=get_settings().app_name, version=__version__, checks=checks
    )
