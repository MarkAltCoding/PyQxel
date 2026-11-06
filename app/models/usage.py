"""Schemas for how much of each provider plan a user's keys have used, for estimates of
what a page would use, and for why a history came back shorter than asked."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

UsageProviderName = Literal["tiingo", "fmp", "eodhd", "finnhub", "alpha_vantage", "anthropic"]
UsageWindow = Literal["second", "minute", "hour", "day", "month"]
UsageUnit = Literal[
    "requests", "calls", "symbols", "bytes", "tokens", "input_tokens", "output_tokens"
]
UsageSource = Literal["provider", "pyqxel"]


class UsageLimit(BaseModel):
    """One limit of a plan, how much of it is used, and when it resets."""

    window: UsageWindow = Field(description="The span the limit counts over.")
    unit: UsageUnit = Field(
        description="What is counted: requests; EODHD's ``calls``, which weigh some requests "
        "more; different ``symbols``; ``bytes`` of answers; or Anthropic's tokens."
    )
    limit: int | None = Field(default=None, description="Most allowed in the window.")
    used: int | None = Field(default=None, description="Used so far in the window.")
    remaining: int | None = Field(default=None, description="Left in the window.")
    resets_at: datetime | None = Field(default=None, description="When the window ends.")
    resets: str = Field(description="When the limit resets, in words.")
    source: UsageSource = Field(
        description="``provider`` when the provider reported the figures, which counts use "
        "from anywhere; ``pyqxel`` when they are PyQxel's own count of its requests alone."
    )
    note: str | None = Field(default=None, description="A caveat about the figures, if any.")


class PlanChoice(BaseModel):
    """A plan the user can say their key is on."""

    id: str
    name: str
    verified: bool = Field(
        description="False when its limits come from third-party listings, as the provider's "
        "page could not be read."
    )


class ProviderUsage(BaseModel):
    """A key's plan, its limits, and how much of each is used."""

    provider: UsageProviderName
    name: str = Field(description="The provider's name for people.")
    plan: str | None = Field(default=None, description="The plan's id, from ``plans``.")
    plan_name: str | None = Field(default=None, description="The plan's name for people.")
    plan_detected: bool = Field(
        description="True when the provider reported the plan's limits itself, so the user "
        "need not choose a plan."
    )
    plans: list[PlanChoice] = Field(
        default_factory=list, description="Plans the user can choose between for this key."
    )
    source: Literal["provider", "pyqxel", "mixed", "none"] = Field(
        description="Where the figures come from: all from the provider, all from PyQxel's "
        "own count, some of each, or nowhere yet."
    )
    limits: list[UsageLimit] = Field(default_factory=list)
    reported_at: datetime | None = Field(
        default=None, description="When the provider last reported figures, if it did."
    )
    note: str | None = Field(default=None, description="What to know about these figures.")


class StoredPrices(BaseModel):
    """The price history PyQxel keeps for the user."""

    symbols: int = Field(ge=0, description="Symbols with stored daily prices.")
    histories: int = Field(ge=0, description="Histories stored; one per symbol and provider.")
    bytes: int = Field(ge=0, description="Their compressed size.")
    oldest_fetch: datetime | None = Field(
        default=None, description="When the oldest of them was last fetched."
    )


class UsageResponse(BaseModel):
    """Every key's plan usage, for the keys the request carried."""

    providers: list[ProviderUsage]
    stored_prices: StoredPrices


EstimateKind = Literal[
    "history",
    "quote",
    "info",
    "fund",
    "news",
    "metrics",
    "signals",
    "alternative",
    "market_news",
    "calendar",
]
"""What a page asks the providers for: price history, quotes, company profiles, a fund's
holdings and costs, or one of the Finnhub-backed sections."""


class PlannedFetch(BaseModel):
    """Something a page would fetch as soon as it loads."""

    kind: EstimateKind
    symbols: list[str] = Field(
        default_factory=list, max_length=80, description="The symbols it is for, if any."
    )
    period: str = Field(
        default="1y", max_length=8, description="For price history: the lookback window."
    )
    interval: str = Field(default="1d", max_length=4, description="For price history: bar size.")


class UsageEstimateRequest(BaseModel):
    """What a page would fetch, to estimate the requests it would send on each key."""

    fetches: list[PlannedFetch] = Field(max_length=40)
    plans: dict[str, str] = Field(
        default_factory=dict,
        description="The plan the user says each key is on, by provider, as in ``GET /usage``.",
    )


class ProviderEstimate(BaseModel):
    """About how many requests a page would send on one key, against what is left."""

    provider: UsageProviderName
    name: str
    requests: int = Field(ge=0, description="About how many requests; caches may save some.")
    new_symbols: int | None = Field(
        default=None,
        ge=0,
        description="For plans that limit different symbols a month: how many of the "
        "symbols have not been asked for this month.",
    )
    tightest: UsageLimit | None = Field(
        default=None,
        description="The limit with the smallest share left, to weigh the requests against.",
    )


class UsageEstimate(BaseModel):
    """About how many requests loading a page would send on each key."""

    providers: list[ProviderEstimate]
    from_store: int = Field(
        ge=0, description="Price histories that would be served from stored prices, free."
    )


ShortfallReason = Literal["limit", "plan", "failed"]


class HistoryShortfall(BaseModel):
    """Why one provider did not serve a fuller price history."""

    provider: str
    name: str
    reason: ShortfallReason = Field(
        description="``limit`` when the key has used its plan's requests for now, ``plan`` "
        "when the plan does not cover the symbol, ``failed`` when the provider failed."
    )
    resets_at: datetime | None = Field(
        default=None, description="For ``limit``: when PyQxel will ask the provider again."
    )
    message: str
