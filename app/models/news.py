"""Schemas for company news from Finnhub or Alpha Vantage, the overview built from it,
and the news an analysis is written from."""

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, PrivateAttr

NewsProvider = Literal["finnhub", "alpha_vantage"]
"""The providers news can come from: Finnhub first, then Alpha Vantage's paid feed."""


class NewsArticle(BaseModel):
    """One news article about a company."""

    published_at: datetime = Field(description="When the article was published, in UTC.")
    headline: str
    summary: str | None = Field(
        default=None,
        description="The publisher's summary, shortened; null when it has none, or only "
        "repeats the headline.",
    )
    source: str = Field(description="Publisher or aggregator, e.g. Reuters.")
    url: str
    category: str | None = Field(default=None, description="Finnhub's category, if any.")
    importance: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="How much the headline looks to matter, from 0 to 1: for market news, "
        "how market-moving it looks; for a company's news, how much it looks to matter for "
        "its shares. Both weigh the topics it names, its publisher, and how recent it is; "
        "null where not scored.",
    )


class CompanyNews(BaseModel):
    """A company's news over a window, newest first, without duplicate headlines."""

    symbol: str
    start: date = Field(description="First day of the window.")
    end: date = Field(description="Last day of the window, today.")
    articles: list[NewsArticle]
    fetched_at: datetime = Field(description="When the provider was asked, in UTC.")
    stale: bool = Field(
        default=False,
        description="Whether Finnhub could not be reached and an earlier answer is shown.",
    )
    provider: NewsProvider = Field(
        default="finnhub",
        description="Where the articles came from: Finnhub's company news, or Alpha "
        "Vantage's market-wide feed (a paid plan), which covers the companies the latest "
        "thousand articles mention.",
    )


class NewsDay(BaseModel):
    """Articles published on one day of the window."""

    day: date
    articles: int = Field(ge=0)


class NewsSource(BaseModel):
    """A publisher and how many of the window's articles it wrote."""

    source: str
    articles: int = Field(ge=1)


class NewsOverview(BaseModel):
    """How much news a company drew over the window, and from whom."""

    total_articles: int = Field(ge=0)
    last_day_articles: int = Field(ge=0, description="Articles in the last 24 hours.")
    last_week_articles: int = Field(ge=0, description="Articles in the last seven days.")
    daily_average: float = Field(ge=0, description="Articles per day over the window.")
    busiest_day: NewsDay | None = Field(
        default=None, description="The day with most articles; null without any."
    )
    latest_at: datetime | None = Field(default=None, description="The newest article's time.")
    sources: list[NewsSource] = Field(description="Publishers with most articles, most first.")
    daily: list[NewsDay] = Field(description="Every day of the window, oldest first.")


class NewsResponse(BaseModel):
    """A company's recent news and its overview."""

    symbol: str
    start: date
    end: date
    overview: NewsOverview
    articles: list[NewsArticle] = Field(description="The newest articles, newest first.")
    fetched_at: datetime
    notice: str | None = Field(
        default=None, description="Says when the news is older than requested, if it is."
    )
    provider: NewsProvider = Field(
        default="finnhub", description="Where the articles came from: Finnhub or Alpha Vantage."
    )


class NewsHeadline(BaseModel):
    """An article as given to Claude: no link, a shorter summary, and for the articles that
    matter most, the article itself."""

    published_at: datetime
    source: str
    headline: str
    summary: str | None = None
    text: str | None = Field(
        default=None,
        description="The article itself, in Markdown, cut to a length, for the articles "
        "that matter most; null when it was not read or its page could not be (a paywall, "
        "a refusal, or too little text).",
    )
    _link: str | None = PrivateAttr(default=None)
    _rank: int | None = PrivateAttr(default=None)

    def linked(self, url: str | None, rank: int | None = None) -> Self:
        """This headline, knowing its article's link and where it stands among those
        chosen (0 for the one that looks to matter most), which choose the articles to
        read. Neither is stored or given to Claude."""
        self._link = url
        self._rank = rank
        return self

    @property
    def link(self) -> str | None:
        """The article's link, if known."""
        return self._link

    @property
    def rank(self) -> int | None:
        """Where the article stands among those chosen, if known."""
        return self._rank


class NewsContext(BaseModel):
    """The news an analysis is written from."""

    start: date
    end: date
    total_articles: int = Field(ge=0, description="Articles in the window, given or not.")
    last_week_articles: int = Field(ge=0)
    articles: list[NewsHeadline] = Field(
        description="The newest articles, newest first; empty when Finnhub has none."
    )
