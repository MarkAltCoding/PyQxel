"""FastAPI application entry point for PyQxel."""

import asyncio
import importlib
import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import health
from app.api.auth import authenticate
from app.api.v1.api import api_router
from app.api.v1.endpoints import account, signin
from app.core.cache import close_cache
from app.core.config import get_settings
from app.core.redact import install_log_redaction, redact
from app.db.session import close_database, init_db
from app.stats.r_bridge import start_r
from app.version import __version__
from app.web import CompressApiResponses, mount_web

__all__ = ["__version__", "app", "create_app"]

logger = logging.getLogger(__name__)

DEFERRED_IMPORTS: tuple[str, ...] = (
    "anthropic",
    "statsmodels.api",
    "scipy.optimize",
    "scipy.special",
    "scipy.stats",
)
"""Packages the app imports where they are used rather than at startup.

Together they are about 2,400 modules, and Cloud Run reads an image's files from the
network the first time they are opened: importing them with the app took over 50
seconds of every cold start, close enough to the startup probe's two minutes that
instances failed to start and visitors were answered 503.
"""


def _import_deferred() -> None:
    """Import :data:`DEFERRED_IMPORTS`, so their first request does not wait for them."""
    for name in DEFERRED_IMPORTS:
        try:
            importlib.import_module(name)
        except Exception:
            # The request that needs the package imports it again and reports the error.
            logger.exception("Could not import %s in the background.", name)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Start R and migrate the database before serving; close shared clients after.

    The packages in :data:`DEFERRED_IMPORTS` are imported in the background once the
    app is serving rather than holding up the start.
    """
    start_r()
    await init_db()
    warm_up = asyncio.create_task(asyncio.to_thread(_import_deferred))
    yield
    await warm_up
    await close_cache()
    await close_database()


def _json_safe(value: object) -> object:
    """``value`` with every NaN and infinity written as a string, which JSON can hold."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return FastAPI's usual 422 for an invalid request.

    Errors echo the input they reject, and a rejected NaN or infinity would otherwise
    make the response itself unserializable, turning the 422 into a 500.
    """
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": _json_safe(jsonable_encoder(exc.errors()))},
    )


async def http_error(request: Request, exc: HTTPException) -> Response:
    """FastAPI's usual error response, with any key or token in the detail scrubbed.

    Details often quote a provider's error, which can quote the URL the user's key was
    sent in.
    """
    if isinstance(exc.detail, str):
        exc.detail = redact(exc.detail)
    return await http_exception_handler(request, exc)


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Returns:
        The configured application with CORS middleware, health checks, the v1 API
        and the web frontend registered.
    """
    install_log_redaction()
    settings = get_settings()
    application = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    application.exception_handler(RequestValidationError)(validation_error)
    application.exception_handler(HTTPException)(http_error)

    application.add_middleware(CompressApiResponses, prefix=settings.api_v1_prefix)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Every API route needs a PyQxel API key; the health checks and the docs stay open.
    application.include_router(health.router)
    # Signing in comes before an account has an API key.
    application.include_router(
        signin.router, prefix=f"{settings.api_v1_prefix}/auth", tags=["sign-in"]
    )
    application.include_router(
        api_router, prefix=settings.api_v1_prefix, dependencies=[Depends(authenticate)]
    )
    account.add_admin_route(application, f"{settings.api_v1_prefix}/me")
    mount_web(application, settings.web_dist_dir)

    return application


app: FastAPI = create_app()
