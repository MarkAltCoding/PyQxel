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

AssetType = Literal["stock", "etf"]
"""What a screened security is: a company's common stock, or an exchange-traded fund
(ETF), which holds many companies. A company's figures, such as its market cap,
valuation, margins and growth, do not apply to an ETF, and are null for one."""

ETF_FIELDS: frozenset[str] = frozenset(
    {"price", "avg_dollar_volume", "return_1m", "return_6m", "momentum_12_1", "volatility",
     "max_drawdown", "beta_market", "beta_size", "beta_value", "beta_momentum",
     "factor_r_squared"}
)  # fmt: skip
"""The screening metrics that apply to an ETF: those of its price, not of a company."""

MarketCapSource = Literal["provider", "sec_shares"]
"""``provider`` when the market data provider, Financial Modeling Prep (or yfinance in
development), reported the market cap, ``sec_shares`` when it was computed as the latest
close times shares outstanding from SEC filings."""

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
    market_cap: float | None = Field(
        default=None, description="Null for an ETF, which is not a company."
    )
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


class ProviderScreenMetrics(BaseModel):
    """A stock's metrics as a market data provider reports them, from Finnhub's free basic
    financials, before they are combined with SEC data and price history."""

    market_cap: float | None = Field(default=None, description="US dollars.")
    pe_ratio: float | None = None
    price_to_sales: float | None = None
    ev_to_ebitda: float | None = None
    fcf_yield: float | None = Field(default=None, description="Inverse of price to FCF.")
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    return_on_equity: float | None = None
    revenue_growth: float | None = None
    earnings_growth: float | None = Field(default=None, description="TTM EPS, year on year.")
    return_6m: float | None = Field(default=None, description="26-week price return.")
    return_52w: float | None = Field(
        default=None, description="52-week price return, standing in for 12-1 momentum."
    )
    volatility_3m: float | None = Field(
        default=None, description="Annualized volatility of daily returns over three months."
    )
    beta: float | None = Field(default=None, description="Beta against the market.")
    average_volume: float | None = Field(
        default=None, description="Shares traded a day, averaged over three months."
    )
    price: float | None = Field(
        default=None, description="The latest price, from a quote, when one was fetched."
    )
    week_52_high: float | None = Field(
        default=None, description="Highest price over 52 weeks, bounding the current price."
    )


class ScreenedStock(ScreenMetrics):
    """A stock in the screened universe and its metrics."""

    symbol: str
    name: str
    exchange: str
    asset_type: AssetType = Field(default="stock", description="A company's stock, or an ETF.")
    sector: str | None = Field(
        default=None, description="Sector, as Financial Modeling Prep classifies it."
    )
    industry: str | None = Field(
        default=None, description="Industry, as Financial Modeling Prep classifies it."
    )
    cik: int | None = Field(
        default=None, description="The SEC registrant behind a stock; null for an ETF."
    )
    market_cap_source: MarketCapSource
    market_cap_rank: int | None = Field(
        default=None, ge=1, description="Rank by market cap in the universe; null for an ETF."
    )
    fundamentals_period_end: date | None = Field(
        default=None, description="End of the latest period the financial ratios use."
    )


class RankedStock(ScreenedStock):
    """A stock matching a screen, with its position in the sorted results."""

    rank: int = Field(ge=1, description="Position in the sorted results, across pages.")


class ScreenFilter(BaseModel):
    """Keeps stocks whose ``field`` lies within the bounds; stocks without it are dropped.

    A P/E filter without ``min`` also drops losses (a negative P/E); a negative ``min``
    keeps them.
    """

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
    """Orders results by ``field``; stocks without it come last, and losses (a negative
    P/E) come after every positive P/E either way."""

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
    asset_types: list[AssetType] = Field(
        default_factory=list,
        max_length=2,
        description="Keep only stocks, only ETFs, or both when empty. A screen on a "
        "company's figures, such as P/E, keeps only stocks, since ETFs have none.",
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
    own: bool = Field(
        default=False,
        description="Whether these come from your own universe, built by your nightly "
        "refresh from your keys, rather than the shared one.",
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
    own: bool = Field(
        default=False,
        description="Whether this is your own universe, built by your nightly refresh from "
        "your keys, rather than the shared one.",
    )
    sectors: list[str] = Field(
        default_factory=list, description="Sectors in the universe, for the ``sectors`` filter."
    )


class SearchMatch(BaseModel):
    """A listing whose symbol or name matches a search: a stock in the screened universe,
    one Finnhub found, or one on the SEC's list of listed companies."""

    symbol: str
    name: str
    exchange: str | None = None
    sector: str | None = None
    market_cap: float | None = Field(default=None, description="In US dollars.")
    kind: str | None = Field(
        default=None, description="Finnhub's type for its matches, e.g. Common Stock or ETP."
    )
    source: Literal["universe", "finnhub", "sec"] = "universe"


class SearchResponse(BaseModel):
    """Stocks matching a search, best matches first."""

    query: str
    items: list[SearchMatch]


PeerGroupKind = Literal["industry", "sector", "universe"]
"""Which stocks a stock is compared with: those in its industry, in its sector when the
industry is too small, or the whole screened universe when the sector is too."""


class PeerMetric(BaseModel):
    """Where one of a stock's screening metrics falls among its peers'."""

    field: ScreenField
    value: float | None = Field(description="The stock's own value; null when not computed.")
    peer_median: float | None = Field(
        default=None, description="Median among peers with the metric; null when too few have it."
    )
    percentile: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Percent of peers with a lower value, counting ties as half: a higher "
        "value ranks higher, whether or not higher is better. Null when the stock lacks the "
        "metric or too few peers have it.",
    )
    peers_with_data: int = Field(ge=0, description="Peers the percentile is computed among.")


class PeerComparison(BaseModel):
    """A stock's screening metrics ranked among its peers in the screened universe.

    A P/E is compared only among profitable peers, and a stock with a loss has no P/E
    percentile.
    """

    group: PeerGroupKind
    group_name: str = Field(description="The industry or sector, or ``screened universe``.")
    peers: int = Field(ge=0, description="Stocks in the group besides this one.")
    market_cap_rank: int = Field(ge=1, description="Rank by market cap in the whole universe.")
    universe_size: int = Field(ge=1, description="Stocks in the screened universe.")
    prices_as_of: date | None = Field(
        default=None, description="Date of the closes the metrics were computed from."
    )
    fundamentals_period_end: date | None = Field(
        default=None, description="End of the latest period the stock's ratios use."
    )
    metrics: list[PeerMetric]
