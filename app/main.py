"""FastAPI application entry point for PyQxel."""

import math
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from app.api import health
from app.api.auth import authenticate
from app.api.v1.api import api_router
from app.core.cache import close_cache
from app.core.config import get_settings
from app.db.session import close_database, init_db
from app.stats.r_bridge import start_r
from app.version import __version__

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


WORKER_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'"
)
"""The policy a Web Worker started from the app's JavaScript runs under.

A worker takes its policy from its own script's response, so this bounds the worker
that runs custom strategies: it may load scripts and fetch only from this origin, where
the app serves its copy of Pyodide, compile WebAssembly for Pyodide, and nothing else.
Without ``'unsafe-eval'``, strategy code cannot reach ``eval`` or ``new Function``
through Pyodide's ``js`` module, nor import code from elsewhere. Browsers ignore the
header on the scripts a page loads, so the rest of the app is unaffected.
"""


class AssetFiles(StaticFiles):
    """The built frontend's fingerprinted assets, with the worker policy on JavaScript."""

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if str(full_path).endswith(".js"):
            response.headers["Content-Security-Policy"] = WORKER_CONTENT_SECURITY_POLICY
        return response


NOT_WEB_PREFIXES: tuple[str, ...] = ("api/", "health", "docs", "redoc", "openapi.json")
"""Paths the web frontend never answers, so a mistyped API route still returns 404."""


def mount_web(application: FastAPI, dist: Path) -> bool:
    """Serve the built web frontend in ``dist`` at ``/``, if it has been built.

    Files are served as they are; any other path gets ``index.html``, so the frontend's
    own routes (``/stocks/AAPL``) load on refresh. Register this after every API route.

    Returns:
        Whether the frontend was found and mounted.
    """
    index = dist / "index.html"
    if not index.is_file():
        return False
    root = dist.resolve()
    if (dist / "assets").is_dir():
        # Vite fingerprints asset names, so they can be cached for good.
        application.mount("/assets", AssetFiles(directory=dist / "assets"), name="assets")

    @application.get("/{path:path}", include_in_schema=False)
    async def web(path: str) -> FileResponse:
        if path.startswith(NOT_WEB_PREFIXES):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
        file = (dist / path).resolve()
        if path and file.is_file() and file.is_relative_to(root):
            return FileResponse(file)
        # Revalidated on every load, so a deploy's new asset names are picked up at once.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return True


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Returns:
        The configured application with CORS middleware, health checks, the v1 API
        and the web frontend registered.
    """
    settings = get_settings()
    application = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    application.exception_handler(RequestValidationError)(validation_error)

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
