"""Schemas for market-wide news and the calendar of events that move markets: FOMC
meetings, major companies' earnings reports, IPOs and market holidays."""

from datetime import date, datetime
from datetime import date as Day
from typing import Literal

from pydantic import BaseModel, Field

from app.models.news import NewsArticle, NewsHeadline

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
        default=None, description="``expected``, ``priced``, ``filed`` or ``withdrawn``."
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
        default=None, description="``pre-market``, ``regular`` or ``post-market``."
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


class MarketContext(BaseModel):
    """The market backdrop an analysis is written against."""

    as_of: date
    market_open: bool | None = None
    fomc_meetings: list[FomcMeeting] = Field(description="The next scheduled FOMC meetings.")
    holidays: list[MarketHoliday] = Field(description="Holidays in the next month.")
    major_earnings: list[EarningsEvent] = Field(
        description="Major companies reporting in the next two weeks, largest first."
    )
    ipos: list[IpoEvent] = Field(description="IPOs in the next two weeks, largest first.")
    headlines: list[NewsHeadline] = Field(
        description="The newest general market headlines, newest first."
    )
