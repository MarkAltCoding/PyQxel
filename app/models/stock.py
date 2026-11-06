"""Schemas describing securities and their market data."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.usage import HistoryShortfall

SYMBOL_PATTERN: str = r"^[A-Za-z0-9.\-^=]{1,15}$"
"""Letters, digits, and the ``.``, ``-``, ``^``, ``=`` used by class shares, indices, and FX."""

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
    quote_type: str | None = Field(
        default=None,
        description="Kind of security as the provider classes it, e.g. EQUITY, ETF, or "
        "MONEYMARKET; null when it does not say.",
    )
    source: str = Field(description="Data provider that produced this record.")
    filled_fields: list[str] = Field(
        default_factory=list,
        description="Fields ``source`` left empty that the SEC (sector and industry), "
        "Finnhub, Tiingo, Alpha Vantage, or a quote filled in.",
    )


class FundWeight(BaseModel):
    """One sector's share of a fund's holdings."""

    name: str = Field(description="The sector, e.g. Information Technology.")
    weight: float = Field(description="Share of the fund's assets, as a decimal.")


class FundHolding(BaseModel):
    """One of a fund's largest holdings."""

    symbol: str | None = Field(default=None, description="The holding's ticker, when it has one.")
    name: str
    weight: float = Field(description="Share of the fund's assets, as a decimal.")
    asset_class: str | None = Field(
        default=None, description="Stocks, Bonds, Cash, or Other, where the source says."
    )


class FundBonds(BaseModel):
    """What a fund's bonds add up to, from its SEC filing."""

    weight: float = Field(description="Share of the fund's net assets in bonds.")
    count: int = Field(ge=0, description="How many bonds it holds.")
    average_coupon: float | None = Field(
        default=None, description="Interest rate the bonds pay, weighted by value, as a decimal."
    )
    average_maturity_years: float | None = Field(
        default=None,
        description="Years until the bonds are due, weighted by value: their stated final "
        "maturity, which mortgage-backed bonds usually beat by being repaid early.",
    )
    issuers: dict[str, float] = Field(
        default_factory=dict,
        description="Share of the bonds by kind of issuer, e.g. US Treasury, as decimals.",
    )


class HoldingsStats(BaseModel):
    """A fund's figures weighted from the companies it holds, as a stock's own are shown.

    Each figure is over the holdings that have it; ``coverage`` says how much of the
    fund's stocks have any. Rates are decimals.
    """

    coverage: float = Field(ge=0, le=1, description="Share of the fund's stocks with figures.")
    pe_ratio: float | None = Field(
        default=None, description="Price over earnings, as a weighted harmonic mean."
    )
    price_to_sales: float | None = Field(
        default=None, description="Price over sales, as a weighted harmonic mean."
    )
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    return_on_equity: float | None = None
    revenue_growth: float | None = Field(default=None, description="TTM revenue, year on year.")
    earnings_growth: float | None = Field(default=None, description="TTM earnings, year on year.")
    average_market_cap: float | None = Field(
        default=None, description="The holdings' market caps, weighted, in US dollars."
    )
    size_mix: dict[str, float] = Field(
        default_factory=dict,
        description="Share in large (over $10B), mid ($2-10B), and small companies.",
    )
    sectors: dict[str, float] = Field(
        default_factory=dict, description="Share in each sector, of the classified holdings."
    )
    sector_coverage: float = Field(
        default=0, ge=0, le=1, description="Share of the fund's stocks with a sector."
    )


class FundProfile(BaseModel):
    """What an ETF or mutual fund holds and costs: the figures that take the place, for a
    fund, of a company's market cap, sector, and earnings.

    Rates are decimals (0.0003 = 0.03%).
    """

    symbol: str
    net_assets: float | None = Field(
        default=None, ge=0, description="The fund's total assets, in US dollars."
    )
    expense_ratio: float | None = Field(
        default=None, description="Share of assets the fund charges each year, after waivers."
    )
    dividend_yield: float | None = Field(
        default=None, description="Distributions over the last year as a share of the price."
    )
    portfolio_turnover: float | None = Field(
        default=None, description="Share of the holdings replaced in a year."
    )
    inception_date: date | None = Field(default=None, description="When the fund started.")
    leveraged: bool | None = Field(
        default=None, description="Whether it borrows or uses derivatives to multiply returns."
    )
    sectors: list[FundWeight] = Field(
        default_factory=list, description="Its holdings' sectors, largest first."
    )
    holdings: list[FundHolding] = Field(
        default_factory=list, description="Its largest holdings, largest first."
    )
    holdings_count: int | None = Field(
        default=None, ge=0, description="How many holdings the provider listed."
    )
    holdings_as_of: date | None = Field(
        default=None, description="The date the holdings are reported as of, where known."
    )
    holdings_source: str | None = Field(
        default=None, description="Where the holdings come from: alpha_vantage or sec."
    )
    asset_mix: list[FundWeight] = Field(
        default_factory=list,
        description="Share of net assets in stocks, bonds, cash, and other, from its SEC filing.",
    )
    bonds: FundBonds | None = Field(default=None, description="Null when it holds few bonds.")
    stats: HoldingsStats | None = Field(
        default=None,
        description="Valuation, profitability, growth, size, and sectors weighted from the "
        "companies it holds; null when none could be looked up, as for a bond fund.",
    )
    stats_as_of: date | None = Field(
        default=None, description="When the companies' figures were current."
    )
    stats_sources: list[str] = Field(
        default_factory=list,
        description="Where the companies' figures come from: your screener, Finnhub.",
    )
    notes: list[str] = Field(
        default_factory=list, description="What is missing and why, in plain sentences."
    )
    source: str = Field(description="Data providers that produced this record.")


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
    short_history: str | None = Field(
        default=None,
        description="Set when a plan served less history than the window asks for, rather "
        "than the listing being younger: a plain warning naming the key that ran out or the "
        "plan that is short, when it resets, and what to do. A backtest should not run on "
        "these bars without the user agreeing.",
    )
    shortfall: list[HistoryShortfall] = Field(
        default_factory=list,
        description="Each provider passed over for a fuller history, and why.",
    )
    stale: bool = Field(
        default=False,
        description="True when the bars are a stored copy that ends before the latest close, "
        "since no key could fetch the days since.",
    )
