"""FastAPI application entry point for PyQxel."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.models.health import HealthResponse

__version__: str = "0.1.0"


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Returns:
        The configured application with CORS middleware and core routes registered.
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

    return application


app: FastAPI = create_app()
