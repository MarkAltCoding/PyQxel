"""Schemas for the nightly refresh of a user's own screener."""

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

NightlyProvider = Literal["finnhub", "tiingo", "fmp", "eodhd", "alpha_vantage"]
"""Providers whose keys the nightly refresh stores and uses. Anthropic's is never stored."""

STORED_PROVIDERS: tuple[NightlyProvider, ...] = (
    "finnhub",
    "tiingo",
    "fmp",
    "eodhd",
    "alpha_vantage",
)
"""The providers in :data:`NightlyProvider`, in the order they are listed."""

RunStatus = Literal["running", "succeeded", "failed"]


class NightlyRefreshSettings(BaseModel):
    """Turns on the nightly refresh, or changes when it runs.

    The keys the request carries are encrypted and stored with it, replacing any stored
    earlier, so send this again after changing a key.
    """

    refresh_time: str = Field(
        default="19:00",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
        description="Local time of day the refresh starts, as HH:MM in 24-hour time. It "
        "starts within the hour after it and takes an hour or two.",
    )
    time_zone: str = Field(
        default="America/New_York",
        max_length=64,
        description="IANA time zone ``refresh_time`` is in, e.g. America/Chicago.",
    )

    @field_validator("time_zone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError, ValueError:
            raise ValueError(f"Unknown time zone {value!r}.") from None
        return value


class NightlyRun(BaseModel):
    """One nightly refresh of the user's own screener."""

    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = Field(default=None, description="Null while it runs.")
    stocks: int | None = Field(default=None, ge=0, description="Stocks in the universe built.")
    priced: int | None = Field(
        default=None,
        ge=0,
        description="Of those, stocks whose price-history columns (momentum, volatility, "
        "drawdown, factor betas) the user's price-history key could fill.",
    )
    prices_as_of: date | None = Field(default=None, description="Date of the latest closes.")
    error: str | None = Field(default=None, description="Why the refresh failed, if it did.")


class NightlyRefreshStatus(BaseModel):
    """Whether the user's nightly refresh is on, what it stores, and how it last went."""

    available: bool = Field(
        description="Whether this server can store keys, which the refresh needs; false "
        "when its owner has not set CREDENTIALS_ENCRYPTION_KEY."
    )
    enabled: bool = Field(description="Whether the user has opted in.")
    refresh_time: str | None = Field(default=None, description="Local HH:MM it starts at.")
    time_zone: str | None = Field(default=None, description="IANA time zone of refresh_time.")
    providers: list[NightlyProvider] = Field(
        default_factory=list, description="Providers whose keys are stored, encrypted."
    )
    keys_updated_at: datetime | None = Field(
        default=None, description="When the stored keys were last replaced."
    )
    next_run_at: datetime | None = Field(
        default=None, description="When the next refresh is due to start."
    )
    latest_run: NightlyRun | None = Field(
        default=None, description="The latest refresh, running, finished or failed."
    )
    last_success: NightlyRun | None = Field(
        default=None, description="The latest refresh that succeeded, whose universe is shown."
    )
