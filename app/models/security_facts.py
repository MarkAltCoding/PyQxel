"""What PyQxel knows of one security beyond its prices, as Claude is given it in stock
reports, portfolio reviews, and Claude's Picks: the screener's figures and where they fall
in the security's sector, how its sector looks, and its recent news and SEC filings."""

from datetime import date

from pydantic import BaseModel, Field

from app.models.news import NewsHeadline
from app.models.screener import AssetType, ScreenMetrics


class ScreenerFacts(ScreenMetrics):
    """What the screener knows of one security, as Claude is given it: its figures,
    everything the screener can filter it by, its next earnings date, and where its
    figures fall in its sector."""

    symbol: str
    name: str
    asset_type: AssetType = "stock"
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_cap_rank: int | None = Field(
        default=None,
        description="Rank by market cap in the screener, a company's share classes counted "
        "once; null for an ETF. The top 500 are the screener's large caps, the top 3,000 "
        "its broad market.",
    )
    sp500: bool | None = Field(
        default=None,
        description="Whether the S&P 500 holds it; null when the index's members could not "
        "be read.",
    )
    fundamentals_period_end: date | None = Field(
        default=None, description="End of the latest period its financial ratios use."
    )
    next_earnings: date | None = Field(
        default=None,
        description="Its next scheduled earnings report within the calendar's window; null "
        "when none is scheduled in it or the calendar could not be fetched.",
    )
    analyst_buy: int | None = Field(
        default=None,
        description="Analysts rating it buy or strong buy in the latest month; null when "
        "its ratings were not fetched.",
    )
    analyst_hold: int | None = Field(default=None, description="Analysts rating it hold.")
    analyst_sell: int | None = Field(
        default=None, description="Analysts rating it sell or strong sell."
    )
    analyst_buy_change: float | None = Field(
        default=None,
        description="The change in the share of analysts rating it a buy since about three "
        "months before, as a decimal (0.1 = ten points more rate it a buy).",
    )
    eps_surprise: float | None = Field(
        default=None,
        description="The latest quarter's earnings surprise, as a decimal of the estimate.",
    )
    eps_beats: int | None = Field(
        default=None,
        description="Quarters of the last four in which earnings beat the estimate.",
    )
    insider_net_value: float | None = Field(
        default=None,
        description="Open-market insider purchases less sales over 90 days, in dollars.",
    )
    pe_ratio_sector_pct: float | None = Field(
        default=None,
        description="Percent of its sector's profitable stocks with a lower P/E (low is "
        "cheap). Every *_sector_pct column is a percentile from 0 to 100 among the stocks of "
        "its sector in the whole screener, counting ties as half; null for an ETF or when "
        "the figure is missing.",
    )
    ev_to_ebitda_sector_pct: float | None = None
    fcf_yield_sector_pct: float | None = None
    revenue_growth_sector_pct: float | None = None
    operating_margin_sector_pct: float | None = None
    return_on_equity_sector_pct: float | None = None
    momentum_12_1_sector_pct: float | None = None
    volatility_sector_pct: float | None = None


class SectorSnapshot(BaseModel):
    """How the stocks of one sector, or of the whole screener, look today: medians of
    the screener's figures among those that have them."""

    sector: str = Field(description="The sector, or ``All stocks`` for the whole screener.")
    stocks: int = Field(ge=0)
    median_pe_ratio: float | None = Field(default=None, description="Among profitable stocks only.")
    median_ev_to_ebitda: float | None = None
    median_fcf_yield: float | None = None
    median_revenue_growth: float | None = None
    median_operating_margin: float | None = None
    median_return_1m: float | None = None
    median_return_6m: float | None = None
    median_momentum_12_1: float | None = None
    median_volatility: float | None = None
    rising_6m: float | None = Field(
        default=None,
        description="Share of its stocks that rose over six months, as a decimal: the "
        "breadth of its trend.",
    )


class CandidateFiling(BaseModel):
    """A filing a candidate made with the SEC recently."""

    form: str = Field(description="Such as 8-K, 10-Q, 10-K, 4 (an insider's trade), or S-3.")
    filed: date
    items: list[str] = Field(
        default_factory=list, description="What an 8-K reports, in words; empty otherwise."
    )


class CandidateEvents(BaseModel):
    """A candidate's own recent news and SEC filings."""

    symbol: str
    news_articles: int | None = Field(
        default=None,
        description="Articles about it in the last 14 days; null when its news could not be "
        "fetched.",
    )
    headlines: list[NewsHeadline] = Field(
        default_factory=list,
        description="Its headlines that look to matter most for its shares, newest first.",
    )
    filings: list[CandidateFiling] = Field(
        default_factory=list,
        description="Its SEC filings of the last 30 days, newest first; empty for an ETF or "
        "when EDGAR could not be read.",
    )
