"""Schemas for service health reporting."""

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Response body for the ``/health`` liveness check."""

    status: Literal["ok"]
    app: str
    version: str


class ReadinessChecks(BaseModel):
    """What each dependency reported."""

    database: Literal["ok", "unavailable", "outdated"] = Field(
        description="``outdated``: reachable, but migrations are pending."
    )
    redis: Literal["ok", "unavailable", "not_configured"] = Field(
        description="Optional: without it caching is off and rate limits are per process."
    )
    r: Literal["ok", "unavailable", "not_started"] = Field(
        description="Optional: without it GARCH endpoints return 503."
    )


class ReadinessResponse(BaseModel):
    """Response body for the ``/health/ready`` readiness check."""

    status: Literal["ok", "degraded", "unavailable"] = Field(
        description="``unavailable`` (sent with 503) when the database cannot serve; "
        "``degraded`` when Redis is configured but down."
    )
    app: str
    version: str
    checks: ReadinessChecks
