"""Schemas for the nightly refresh of a user's own screener."""

from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

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

NightlyUniverse = Literal["us_stocks", "us_etfs", "nasdaq", "sp500", "nasdaq100", "international"]
"""Sets of securities a refresh can rebuild, combined as the user chooses: every stock on a
US exchange, every ETF on one, the stocks listed on Nasdaq, the companies in the S&P 500
or the Nasdaq-100, and foreign companies' stocks listed in the US. The fewer securities,
the fewer Finnhub requests, so the sooner it finishes."""

EVERYTHING: list[NightlyUniverse] = ["us_stocks", "us_etfs"]
"""Every US stock and ETF: what a refresh rebuilds unless the user chooses."""

MAX_CUSTOM_SYMBOLS: int = 100
"""Most securities a user may add to their refresh by ticker."""

CUSTOM_SYMBOL_PATTERN: str = r"^[A-Z0-9][A-Z0-9.\-]{0,15}$"
"""A ticker as PyQxel writes it: BRK-B, or 7203.T for a listing abroad."""

PriceKey = Literal["tiingo", "fmp", "eodhd", "alpha_vantage"]
"""Providers whose keys fetch price histories, which each have their own nightly limit."""

PRICE_KEY_ORDER: tuple[PriceKey, ...] = ("tiingo", "fmp", "eodhd", "alpha_vantage")
"""Price-history keys in the order a refresh uses them, cheapest first."""

FREE_PLAN_HISTORIES: dict[PriceKey, int] = {
    "tiingo": 50,
    "fmp": 250,
    "eodhd": 20,
    "alpha_vantage": 25,
}
"""Price histories each key fetches a night unless the user chooses: what its free plan
answers before it stops, Tiingo's in an hour and the others' in a day."""

MAX_PRICE_HISTORIES: int = 10_000
"""Most price histories a user may ask of one key a night, more than are listed."""

PriceLimit = Annotated[int, Field(ge=0, le=MAX_PRICE_HISTORIES)] | None
"""Price histories one key may fetch a night; ``None`` for as many as its plan allows."""


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
    price_history_limits: dict[PriceKey, PriceLimit] = Field(
        default_factory=dict,
        description="Most price histories each price-history key fetches a night, one "
        "request per stock or ETF, largest first, the cheapest key first and each key "
        "taking the next ones. Null for as many as that key's plan allows, which can use "
        "up a free plan's day; a key left out fetches what its free plan allows (Tiingo "
        "50, FMP 250, EODHD 20, Alpha Vantage 25).",
    )
    universes: list[NightlyUniverse] = Field(
        default_factory=lambda: list(EVERYTHING),
        max_length=6,
        description="The sets each refresh rebuilds, combined: us_stocks (every stock on a "
        "US exchange), us_etfs (every ETF on one), nasdaq (stocks listed on Nasdaq), sp500 "
        "and nasdaq100 (those indexes' companies), and international (foreign companies' "
        "stocks listed in the US). A security in several is fetched once.",
    )
    custom_symbols: list[Annotated[str, StringConstraints(pattern=CUSTOM_SYMBOL_PATTERN)]] = Field(
        default_factory=list,
        max_length=MAX_CUSTOM_SYMBOLS,
        description="Tickers of stocks and ETFs to refresh as well, such as one not in "
        "the sets chosen. A listing abroad, such as 7203.T, is refreshed through its "
        "company's US listing, since free plans have no figures for it.",
    )

    @field_validator("universes")
    @classmethod
    def _distinct_universes(cls, value: list[NightlyUniverse]) -> list[NightlyUniverse]:
        return list(dict.fromkeys(value))

    @field_validator("custom_symbols", mode="before")
    @classmethod
    def _upper_symbols(cls, value: object) -> object:
        if isinstance(value, list):
            cleaned = [item.strip().upper() if isinstance(item, str) else item for item in value]
            return list(dict.fromkeys(cleaned))
        return value

    @model_validator(mode="after")
    def _something_chosen(self) -> "NightlyRefreshSettings":
        if not self.universes and not self.custom_symbols:
            raise ValueError("Choose at least one set of securities or add a ticker.")
        return self

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
    notes: list[str] = Field(
        default_factory=list,
        description="What the refresh could not do as asked, such as a ticker added that "
        "no key had figures for, and what it did instead.",
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
    price_history_limits: dict[PriceKey, PriceLimit] = Field(
        default_factory=dict,
        description="Most price histories each stored price-history key fetches a night; "
        "null for as many as its plan allows.",
    )
    universes: list[NightlyUniverse] = Field(
        default_factory=lambda: list(EVERYTHING),
        description="The sets each refresh rebuilds; every US stock and ETF while it is off.",
    )
    custom_symbols: list[str] = Field(
        default_factory=list, description="Tickers each refresh adds to those sets."
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
