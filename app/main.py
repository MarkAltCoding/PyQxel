"""FastAPI application entry point for PyQxel."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai.agent import close_anthropic_client
from app.api.v1.api import api_router
from app.core.cache import close_cache
from app.core.config import get_settings
from app.db.session import close_database, init_db
from app.models.health import HealthResponse
from app.stats.r_bridge import start_r

__version__: str = "0.1.0"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Start R and create database tables before serving; close shared clients after."""
    start_r()
    await init_db()
    yield
    await close_anthropic_client()
    await close_cache()
    await close_database()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Returns:
        The configured application with CORS middleware, core routes and the v1 API registered.
    """
    settings = get_settings()
    application = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        """Report that the service is up."""
        return HealthResponse(status="ok", app=settings.app_name, version=__version__)

    application.include_router(api_router, prefix=settings.api_v1_prefix)

    return application


app: FastAPI = create_app()
