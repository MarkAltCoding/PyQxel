"""FastAPI application entry point for PyQxel."""

import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import health
from app.api.auth import authenticate
from app.api.v1.api import api_router
from app.core.cache import close_cache
from app.core.config import get_settings
from app.db.session import close_database, init_db
from app.stats.r_bridge import start_r
from app.version import __version__
from app.web import CompressApiResponses, mount_web

__all__ = ["__version__", "app", "create_app"]


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Start R and create database tables before serving; close shared clients after."""
    start_r()
    await init_db()
    yield
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


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Returns:
        The configured application with CORS middleware, health checks, the v1 API
        and the web frontend registered.
    """
    settings = get_settings()
    application = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    application.exception_handler(RequestValidationError)(validation_error)

    application.add_middleware(CompressApiResponses, prefix=settings.api_v1_prefix)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Every API route needs a PyQxel API key; the health checks and the docs stay open.
    application.include_router(health.router)
    application.include_router(
        api_router, prefix=settings.api_v1_prefix, dependencies=[Depends(authenticate)]
    )
    mount_web(application, settings.web_dist_dir)

    return application


app: FastAPI = create_app()
