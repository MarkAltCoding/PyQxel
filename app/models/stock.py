"""Schemas describing securities and their market data."""

from pydantic import BaseModel, Field


class TickerInfo(BaseModel):
    """Normalized descriptive and pricing snapshot for a single ticker."""

    symbol: str
    name: str | None = None
    currency: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_cap: float | None = Field(default=None, ge=0)
    price: float | None = Field(default=None, ge=0)
    source: str = Field(description="Data provider that produced this record.")
