"""Schemas for company news from Finnhub, the overview built from it, and the news an
analysis is written from."""

from datetime import date, datetime

from pydantic import BaseModel, Field


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


class CompanyNews(BaseModel):
    """A company's news over a window, newest first, without duplicate headlines."""

    symbol: str
    start: date = Field(description="First day of the window.")
    end: date = Field(description="Last day of the window, today.")
    articles: list[NewsArticle]
    fetched_at: datetime = Field(description="When Finnhub was asked, in UTC.")
    stale: bool = Field(
        default=False,
        description="Whether Finnhub could not be reached and an earlier answer is shown.",
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


class NewsHeadline(BaseModel):
    """An article as given to Claude: no link, and a shorter summary."""

    published_at: datetime
    source: str
    headline: str
    summary: str | None = None


class NewsContext(BaseModel):
    """The news an analysis is written from."""

    start: date
    end: date
    total_articles: int = Field(ge=0, description="Articles in the window, given or not.")
    last_week_articles: int = Field(ge=0)
    articles: list[NewsHeadline] = Field(
        description="The newest articles, newest first; empty when Finnhub has none."
    )
