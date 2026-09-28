"""Schemas for service health reporting."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response body for the ``/health`` liveness check."""

    status: Literal["ok"]
    app: str
    version: str
