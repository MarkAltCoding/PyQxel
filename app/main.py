"""FastAPI application entry point for PyQxel."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.api import api_router
from app.core.config import get_settings
from app.models.health import HealthResponse

__version__: str = "0.1.0"


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Returns:
        The configured application with CORS middleware, core routes and the v1 API registered.
    """
    settings = get_settings()
    application = FastAPI(title=settings.app_name, version=__version__)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
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
