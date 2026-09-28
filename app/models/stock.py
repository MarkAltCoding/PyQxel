"""Schemas describing securities and their market data."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

HistoryPeriod = Literal["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"]
"""Lookback periods accepted by yfinance."""

HistoryInterval = Literal[
    "1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"
]
"""Bar sizes accepted by yfinance."""

HistoryCoverage = Literal["full", "partial", "none"]
"""How much of a requested history window has data."""


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


class OHLCVBar(BaseModel):
    """A single adjusted price bar. Fields other than ``close`` may be missing upstream."""

    timestamp: datetime
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    volume: int | None = Field(default=None, ge=0)


class PriceHistory(BaseModel):
    """Adjusted OHLCV history for a ticker, sorted oldest first."""

    symbol: str
    period: HistoryPeriod
    interval: HistoryInterval
    bars: list[OHLCVBar]
    coverage: HistoryCoverage = Field(
        description="``full`` when bars span the window, ``partial`` when data begins after "
        "the window starts, ``none`` when the symbol has no bars in the window."
    )
    notice: str | None = Field(
        default=None, description="Explains which part of the window has no data, if any."
    )
