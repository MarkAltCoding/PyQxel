"""Schemas for market-wide news and the calendar of events that move markets: FOMC
meetings, major companies' earnings reports, IPOs, and market holidays."""

from datetime import date, datetime
from datetime import date as Day
from typing import Literal

from pydantic import BaseModel, Field

from app.models.news import NewsArticle, NewsHeadline, NewsProvider

MarketNewsCategory = Literal["general", "forex", "crypto", "merger"]
"""Finnhub's market news categories; ``merger`` is mergers and acquisitions."""


class FomcMeeting(BaseModel):
    """A scheduled meeting of the Federal Reserve's Federal Open Market Committee."""

    start: date
    end: date = Field(description="The decision is announced at 2 p.m. Eastern on this day.")
    projections: bool = Field(
        description="Whether the Summary of Economic Projections is released with it."
    )


class EarningsEvent(BaseModel):
    """A company's scheduled earnings report."""

    symbol: str
    name: str | None = None
    date: Day
    hour: str | None = Field(
        default=None, description="``bmo`` before the open, ``amc`` after the close."
    )
    quarter: int | None = None
    year: int | None = None
    eps_estimate: float | None = None
    revenue_estimate: float | None = None
    market_cap: float | None = Field(
        default=None, description="From the screened universe, when the company is in it."
    )


class IpoEvent(BaseModel):
    """An initial public offering on the calendar."""

    date: Day
    symbol: str | None = None
    name: str
    exchange: str | None = None
    shares: float | None = None
    price: str | None = Field(default=None, description="The price or range, e.g. 10.00-12.00.")
    status: str | None = Field(
        default=None, description="``expected``, ``priced``, ``filed``, or ``withdrawn``."
    )
    value: float | None = Field(default=None, description="Shares times price, in US dollars.")


class MarketHoliday(BaseModel):
    """A day US exchanges close, or close early."""

    date: Day
    name: str
    trading_hours: str | None = Field(
        default=None, description="The shortened session, e.g. 09:30-13:00; null when closed."
    )


class MarketStatus(BaseModel):
    """Whether US exchanges are trading now."""

    is_open: bool
    session: str | None = Field(
        default=None, description="``pre-market``, ``regular``, or ``post-market``."
    )
    holiday: str | None = None
    as_of: datetime


class MarketCalendar(BaseModel):
    """Market-moving dates over a window, each list in date order."""

    start: date
    end: date
    fomc: list[FomcMeeting]
    earnings: list[EarningsEvent] = Field(
        description="Major companies' earnings reports: a market cap of ``min_market_cap`` "
        "or more in the screened universe or, for companies outside it, a quarterly "
        "revenue estimate of a fifth of it or more."
    )
    ipos: list[IpoEvent]
    holidays: list[MarketHoliday]
    status: MarketStatus | None = None
    notice: str | None = Field(
        default=None, description="Says which parts could not be supplied, if any."
    )


class MarketNews(BaseModel):
    """The latest market-wide news in a category, newest first."""

    category: MarketNewsCategory
    articles: list[NewsArticle]
    fetched_at: datetime
    provider: NewsProvider = Field(
        default="finnhub",
        description="Where the articles came from: Finnhub, or Alpha Vantage's feed (a "
        "paid plan) when Finnhub cannot supply them.",
    )


class YieldPoint(BaseModel):
    """The Treasury's par yield at one maturity on three dates."""

    tenor: str = Field(description="The maturity, as the Treasury names it: 3 Mo, 2 Yr, 10 Yr.")
    latest: float = Field(description="The latest yield, in percent.")
    month_ago: float | None = Field(default=None, description="About a month before.")
    year_ago: float | None = Field(default=None, description="About a year before.")


class EconomyContext(BaseModel):
    """The economy's backdrop, from the Treasury and the Bureau of Labor Statistics."""

    yields_as_of: Day | None = Field(default=None, description="The latest yield curve's date.")
    yields: list[YieldPoint] = Field(
        default_factory=list, description="The yield curve, shortest maturity first."
    )
    spread_10y_2y: float | None = Field(
        default=None,
        description="The 10-year yield less the 2-year, in percentage points; below zero, "
        "an inverted curve, which has often come before recessions.",
    )
    spread_10y_3m: float | None = Field(
        default=None, description="The 10-year yield less the 3-month, in percentage points."
    )
    inflation_month: str | None = Field(
        default=None, description="The month the latest consumer prices are for, YYYY-MM."
    )
    cpi_yoy: float | None = Field(
        default=None, description="Consumer prices over twelve months, as a decimal."
    )
    core_cpi_yoy: float | None = Field(
        default=None, description="The same without food and energy, as a decimal."
    )
    core_cpi_3m_annualized: float | None = Field(
        default=None,
        description="Core prices over the last three months, at a yearly rate, as a decimal: "
        "the recent direction of inflation.",
    )
    unemployment: float | None = Field(
        default=None, description="The latest unemployment rate, as a decimal."
    )
    unemployment_year_ago: float | None = None
    payrolls_change: float | None = Field(
        default=None, description="Jobs added in the latest month, in thousands."
    )
    payrolls_change_3m: float | None = Field(
        default=None, description="Jobs added a month on average over three months, thousands."
    )
    jobs_month: str | None = Field(default=None, description="The month the jobs figures are for.")
    sources: str | None = Field(
        default=None,
        description="Where the figures come from and when they were retrieved, to cite with "
        "them, as the BLS asks.",
    )


class MarketContext(BaseModel):
    """The market backdrop an analysis is written against."""

    as_of: date
    market_open: bool | None = None
    fomc_meetings: list[FomcMeeting] = Field(
        description="The next scheduled FOMC meetings: the next two, or, for Claude's Picks "
        "and portfolio reviews, every one scheduled within the holding period."
    )
    holidays: list[MarketHoliday] = Field(
        description="Market holidays ahead: the next month's, or for picks and reviews, "
        "those within the calendar window (up to 90 days)."
    )
    major_earnings: list[EarningsEvent] = Field(
        description="Major companies reporting in the next two weeks, or for picks and "
        "reviews within the calendar window, largest first."
    )
    ipos: list[IpoEvent] = Field(
        description="IPOs in the next two weeks, or for picks and reviews within the "
        "calendar window, largest first."
    )
    economy: EconomyContext | None = Field(
        default=None,
        description="The yield curve, inflation, and jobs; null when neither the Treasury "
        "nor the Bureau of Labor Statistics could be reached.",
    )
    calendar_days: int | None = Field(
        default=None,
        description="Days ahead the earnings reports, IPOs, and holidays cover, for picks "
        "and reviews; null for a stock report's two weeks.",
    )
    headlines: list[NewsHeadline] = Field(
        description="The general market headlines that look most market-moving, newest " "first."
    )
