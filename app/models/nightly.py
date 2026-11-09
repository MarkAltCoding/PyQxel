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

DEFAULT_PRICE_HISTORY_STOCKS: int = 40
"""Price histories a refresh fetches unless the user chooses: enough for the largest
stocks, and under a free Tiingo key's 50 requests an hour."""


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

    use_keys: list[NightlyProvider] | None = Field(
        default=None,
        description="The keys the refresh may use and so stores, of those the request "
        "carries; Finnhub's is needed. Null for every one the request carries.",
    )
    price_history_stocks: int | None = Field(
        default=DEFAULT_PRICE_HISTORY_STOCKS,
        ge=0,
        le=10_000,
        description="Most stocks to fetch price histories for each night, largest first, "
        "one request each on the price-history keys; null for as many as their plans "
        "allow, which can use up a free plan's day.",
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
    usage: dict[str, int] | None = Field(
        default=None, description="Requests the refresh sent on each key, by provider."
    )


class NightlyKeyUse(BaseModel):
    """About how many requests one refresh sends on one key."""

    provider: NightlyProvider
    requests: int = Field(ge=0, description="About how many requests one refresh sends.")
    detail: str = Field(description="What the requests are for.")


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
        default=None, description="The latest refresh, running, finished, or failed."
    )
    last_success: NightlyRun | None = Field(
        default=None, description="The latest refresh that succeeded, whose universe is shown."
    )
    price_history_stocks: int | None = Field(
        default=DEFAULT_PRICE_HISTORY_STOCKS,
        description="Most stocks given price histories each night; null for as many as the "
        "keys' plans allow.",
    )
    key_use: list[NightlyKeyUse] = Field(
        default_factory=list,
        description="About how many requests one refresh sends on each stored key, to weigh "
        "against its plan's limits.",
    )


class NightlyTriggerResponse(BaseModel):
    """What Cloud Scheduler's hourly call found: whether a refresh was due, and so the
    refresh job was started."""

    started: bool = Field(
        description="Whether a user's refresh was due, so the nightly refresh job was started."
    )
