"""Schemas describing securities and their market data."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.usage import HistoryShortfall

SYMBOL_PATTERN: str = r"^[A-Za-z0-9.\-^=]{1,15}$"
"""Letters, digits and the ``.``, ``-``, ``^``, ``=`` used by class shares, indices and FX."""

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
        description="Kind of security as the provider classes it, e.g. EQUITY, ETF or "
        "MONEYMARKET; null when it does not say.",
    )
    source: str = Field(description="Data provider that produced this record.")
    filled_fields: list[str] = Field(
        default_factory=list,
        description="Fields ``source`` left empty that Finnhub or Alpha Vantage filled in.",
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


class FundProfile(BaseModel):
    """What an ETF or mutual fund holds and costs: the figures that take the place, for a
    fund, of a company's market cap, sector and earnings.

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
