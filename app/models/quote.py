"""Schemas for live quotes and the messages of the quote WebSocket."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.models.stock import SYMBOL_PATTERN

MAX_SUBSCRIPTIONS: int = 25
"""Most symbols one WebSocket connection may follow at once."""

SubscribedSymbol = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=SYMBOL_PATTERN)
]


class Quote(BaseModel):
    """The latest price of a ticker and its move since the previous close.

    ``change_percent`` is a decimal (0.012 = 1.2%), like every other return in the API.
    """

    symbol: str
    price: float = Field(ge=0)
    previous_close: float | None = Field(default=None, gt=0)
    change: float | None = Field(default=None, description="Price minus previous close.")
    change_percent: float | None = Field(
        default=None, description="Change as a decimal fraction of the previous close."
    )
    day_high: float | None = Field(default=None, ge=0)
    day_low: float | None = Field(default=None, ge=0)
    volume: int | None = Field(default=None, ge=0, description="Shares traded today.")
    currency: str | None = None
    as_of: datetime = Field(description="When the quote was fetched (UTC).")
    source: str = Field(description="Data provider that produced this quote.")


class QuoteCommand(BaseModel):
    """A client message changing which symbols a quote connection follows."""

    action: Literal["subscribe", "unsubscribe"]
    symbols: list[SubscribedSymbol] = Field(min_length=1, max_length=MAX_SUBSCRIPTIONS)


class QuoteUpdate(BaseModel):
    """Server message carrying a quote that is new or has changed since the last one sent."""

    type: Literal["quote"] = "quote"
    quote: Quote


class SubscriptionState(BaseModel):
    """Server message listing the symbols the connection now follows."""

    type: Literal["subscriptions"] = "subscriptions"
    symbols: list[str]


class QuoteStreamError(BaseModel):
    """Server message reporting a rejected command or a symbol that could not be quoted.

    The connection stays open; ``symbol`` is set when the error concerns one symbol.
    """

    type: Literal["error"] = "error"
    detail: str
    symbol: str | None = None
