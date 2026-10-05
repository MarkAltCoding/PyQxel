"""Schemas for the stock screener: the screened universe, its metrics, and screen requests.

Returns, growth, margins, yields and volatilities are decimals (0.25 = 25%); volatility
is annualized. Money amounts are US dollars.
"""

from datetime import date, datetime
from typing import Literal, Self, get_args

from pydantic import BaseModel, Field, model_validator

UniverseTier = Literal["all", "large_cap", "broad_market", "liquid"]
"""Which part of the screened universe a screen covers:

* ``all``: every stock that passed the baseline filter when the universe was refreshed.
* ``large_cap``: the 500 largest by market cap, approximating the S&P 500.
* ``broad_market``: the 3,000 largest by market cap, approximating the Russell 3000.
* ``liquid``: stocks trading at least $5M a day on average.
"""

LARGE_CAP_COUNT: int = 500
BROAD_MARKET_COUNT: int = 3_000
LIQUID_DOLLAR_VOLUME: float = 5_000_000.0

MarketCapSource = Literal["provider", "sec_shares"]
"""``provider`` when a market data provider (Nasdaq or yfinance) reported the market cap,
``sec_shares`` when it was computed as the latest close times shares outstanding from SEC
filings."""

ScreenField = Literal[
    "price",
    "market_cap",
    "avg_dollar_volume",
    "pe_ratio",
    "price_to_sales",
    "ev_to_ebitda",
    "fcf_yield",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "return_on_equity",
    "revenue_growth",
    "earnings_growth",
    "return_1m",
    "return_6m",
    "momentum_12_1",
    "volatility",
    "max_drawdown",
    "beta_market",
    "beta_size",
    "beta_value",
    "beta_momentum",
    "factor_r_squared",
]
"""Metrics a screen can filter and sort on."""


class ScreenMetrics(BaseModel):
    """A stock's precomputed screening metrics; null when they could not be computed."""

    price: float = Field(description="Latest adjusted close.")
    market_cap: float
    avg_dollar_volume: float = Field(
        description="Average close times volume over the last three months."
    )
    pe_ratio: float | None = None
    price_to_sales: float | None = None
    ev_to_ebitda: float | None = None
    fcf_yield: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    return_on_equity: float | None = None
    revenue_growth: float | None = Field(default=None, description="TTM revenue, year on year.")
    earnings_growth: float | None = Field(
        default=None, description="TTM net income, year on year; null after a loss."
    )
    return_1m: float | None = Field(default=None, description="Return over 21 trading days.")
    return_6m: float | None = Field(default=None, description="Return over 126 trading days.")
    momentum_12_1: float | None = Field(
        default=None,
        description="Return from 12 months to 1 month ago, skipping the latest month as "
        "momentum strategies do.",
    )
    volatility: float | None = Field(
        default=None, description="Annualized volatility of daily returns over a year."
    )
    max_drawdown: float | None = Field(
        default=None, le=0, description="Largest peak-to-trough decline over a year."
    )
    beta_market: float | None = Field(default=None, description="Carhart Mkt-RF beta.")
    beta_size: float | None = Field(default=None, description="Carhart SMB beta.")
    beta_value: float | None = Field(default=None, description="Carhart HML beta.")
    beta_momentum: float | None = Field(default=None, description="Carhart Mom beta.")
    factor_r_squared: float | None = Field(
        default=None, description="Share of return variance the four factors explain."
    )


class ScreenedStock(ScreenMetrics):
    """A stock in the screened universe and its metrics."""

    symbol: str
    name: str
    exchange: str
    sector: str | None = Field(default=None, description="Sector, as Nasdaq classifies it.")
    industry: str | None = Field(default=None, description="Industry, as Nasdaq classifies it.")
    cik: int
    market_cap_source: MarketCapSource
    market_cap_rank: int = Field(ge=1, description="Rank by market cap in the universe.")
    fundamentals_period_end: date | None = Field(
        default=None, description="End of the latest period the financial ratios use."
    )


class RankedStock(ScreenedStock):
    """A stock matching a screen, with its position in the sorted results."""

    rank: int = Field(ge=1, description="Position in the sorted results, across pages.")


class ScreenFilter(BaseModel):
    """Keeps stocks whose ``field`` lies within the bounds; stocks without it are dropped."""

    field: ScreenField
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _has_a_bound(self) -> Self:
        if self.min is None and self.max is None:
            raise ValueError("A filter needs min, max or both.")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must not exceed max.")
        return self


class ScreenSort(BaseModel):
    """Orders results by ``field``; stocks without it come last."""

    field: ScreenField = "market_cap"
    descending: bool = True


class ScreenRequest(BaseModel):
    """A screen: which universe, which filters, and how to order and page the results."""

    universe: UniverseTier = "all"
    sectors: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Keep only these sectors, e.g. Technology; all when empty.",
    )
    industries: list[str] = Field(
        default_factory=list, max_length=200, description="Keep only these industries."
    )
    filters: list[ScreenFilter] = Field(default_factory=list, max_length=len(get_args(ScreenField)))
    sort: ScreenSort = Field(default_factory=ScreenSort)
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class ScreenResponse(BaseModel):
    """One page of stocks matching a screen."""

    items: list[RankedStock]
    total: int = Field(ge=0, description="Stocks matching the screen, on any page.")
    limit: int
    offset: int
    refreshed_at: datetime | None = Field(
        default=None, description="When the universe's metrics were last computed."
    )


class UniverseStatus(BaseModel):
    """The state of the screened universe and its last refresh."""

    stocks: int = Field(ge=0, description="Stocks in the universe.")
    refreshed_at: datetime | None = Field(
        default=None, description="When the metrics were last computed; null if never."
    )
    prices_as_of: date | None = Field(default=None, description="Date of the latest closes.")
    fundamentals_refreshed_at: datetime | None = None
    factor_data_end: date | None = Field(
        default=None, description="Last date of the factor data the betas are estimated on."
    )
    last_error: str | None = Field(
        default=None, description="Why the latest refresh failed, if it did."
    )
