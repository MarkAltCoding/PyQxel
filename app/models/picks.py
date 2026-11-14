"""Schemas for Claude's picks: the securities Claude would buy now and hold for a period
the person chooses, picked from the screener's universe.

:class:`PicksReport` doubles as the structured-output schema Claude fills in, so it uses
only plain types and literals.
"""

from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.core.text import listed
from app.models.markets import MarketContext
from app.models.screener import AssetType, ScreenedStock, ScreenMetrics
from app.models.web import WebResearch

WEEKS_PER_YEAR: float = 52.18
"""Weeks in an average year."""

WEEKS_PER_MONTH: float = WEEKS_PER_YEAR / 12
"""Weeks in an average month."""

MAX_HORIZON_YEARS: int = 60
"""Longest holding period, as a portfolio review's horizon."""

MIN_PICKS: int = 3
"""Fewest picks a list may hold; Claude is asked for five to ten."""

MAX_PICKS: int = 12
"""Most picks kept from an answer."""

MAX_CANDIDATES: int = 400
"""Most candidates the screener offers Claude for one list of picks."""


def _plural(count: int, unit: str) -> str:
    return f"{count} {unit}{'' if count == 1 else 's'}"


class Horizon(BaseModel):
    """How long the picks are to be held: any mix of years, months, and weeks, from one
    week up."""

    years: int = Field(default=0, ge=0, le=MAX_HORIZON_YEARS)
    months: int = Field(default=0, ge=0, le=MAX_HORIZON_YEARS * 12)
    weeks: int = Field(default=0, ge=0, le=MAX_HORIZON_YEARS * 53)

    @model_validator(mode="after")
    def _within_bounds(self) -> Self:
        if self.total_weeks < 1:
            raise ValueError("The holding period must be at least one week.")
        if self.total_weeks > MAX_HORIZON_YEARS * WEEKS_PER_YEAR + 1e-6:
            raise ValueError(f"The holding period may be at most {MAX_HORIZON_YEARS} years.")
        return self

    @property
    def total_weeks(self) -> float:
        """The whole period in weeks, a month being a twelfth of a year."""
        return self.years * WEEKS_PER_YEAR + self.months * WEEKS_PER_MONTH + self.weeks

    @property
    def label(self) -> str:
        """The period in words, such as "1 year, 6 months, and 2 weeks"."""
        parts = [
            _plural(count, unit)
            for count, unit in ((self.years, "year"), (self.months, "month"), (self.weeks, "week"))
            if count
        ]
        return listed(parts)


class PicksProfile(BaseModel):
    """What the picks are tailored to. Only the holding period is required."""

    horizon: Horizon = Field(
        default_factory=lambda: Horizon(years=1),
        description="How long the picks are to be held, from one week up.",
    )
    risk_tolerance: Literal["conservative", "moderate", "aggressive"] | None = None
    amount: float | None = Field(
        default=None,
        gt=0,
        le=1e9,
        allow_inf_nan=False,
        description="Money to invest in the picks, in dollars; each pick's share of it is "
        "then given in dollars too.",
    )
    asset_types: list[AssetType] = Field(
        default_factory=list,
        max_length=2,
        description="Pick only stocks, only ETFs, or either when empty.",
    )
    notes: str | None = Field(
        default=None,
        max_length=1_000,
        description="Anything else to take into account, e.g. sectors to favor or avoid, "
        "dividends wanted, or securities already held.",
    )


class PicksRequest(BaseModel):
    """Options for a list of Claude's picks."""

    profile: PicksProfile = Field(default_factory=PicksProfile)
    web_search: bool = Field(
        default=False,
        description="Let Claude search the web first, for news and the backdrop the screener "
        "cannot show. Every fact it brings back is listed with its source. Anthropic bills "
        "each search to your key, on top of the tokens.",
    )
    refresh: bool = Field(
        default=False,
        description="Pick anew even when picks for the same profile and screener data are "
        "recent enough to reuse.",
    )


class PickCandidate(ScreenMetrics):
    """A security the screener offered Claude, with the measures it led on."""

    symbol: str
    name: str
    asset_type: AssetType = "stock"
    sector: str | None = None
    industry: str | None = None
    lenses: list[str] = Field(
        description="The measures this security led the screener on, such as 12-month "
        "momentum or free cash flow yield."
    )


class PicksUniverse(BaseModel):
    """The screener the candidates were drawn from."""

    securities: int = Field(ge=0, description="Securities in the screener.")
    own: bool = Field(description="Whether it is the person's own, built by their nightly refresh.")
    prices_as_of: date | None = Field(default=None, description="Date of the latest closes.")
    refreshed_at: datetime | None = None
    has_etfs: bool = Field(description="Whether the screener holds ETFs as well as stocks.")


class PicksContext(BaseModel):
    """The data a list of picks is written from. All of it is sent to Claude, the
    candidates as a table."""

    profile: PicksProfile
    horizon: str = Field(description="The holding period in words.")
    horizon_weeks: float = Field(gt=0, description="The holding period in weeks.")
    today: date
    universe: PicksUniverse
    candidates: list[PickCandidate] = Field(max_length=MAX_CANDIDATES)
    market: MarketContext | None = Field(
        default=None,
        description="The market backdrop: upcoming FOMC meetings, market holidays, major "
        "companies' earnings reports, and IPOs, and the newest market headlines.",
    )
    web_research: WebResearch | None = Field(
        default=None,
        description="What a web search made for these picks found, each fact with its "
        "sources; null when the web was not searched.",
    )
    notice: str | None = Field(default=None, description="Anything missing from the data.")


class Pick(BaseModel):
    """One security Claude would buy and hold for the period."""

    symbol: str = Field(description="Ticker, as in the candidates table.")
    name: str = Field(description="The company's or fund's name.")
    kind: Literal["stock", "etf"]
    conviction: Literal["low", "medium", "high"] = Field(
        description="How firmly the evidence supports this pick for this period."
    )
    weight: float = Field(
        ge=0, le=1, description="Its share of the money invested, as a decimal; weights sum to 1."
    )
    thesis: str = Field(
        description="Why this pick, for this period and profile, citing the figures behind it."
    )
    risk: str = Field(description="The main risk, and what would prove the pick wrong.")
    entry: str = Field(
        description="How to buy it: the order type, and at once or in steps, in a sentence."
    )
    exit: str = Field(
        description="When to sell or look again: at the end of the period, and on what "
        "earlier sign, in a sentence."
    )


class PicksReport(BaseModel):
    """Claude's picks for a holding period, ranked, with how they were chosen."""

    headline: str = Field(description="One sentence naming the idea behind the picks.")
    market_view: str = Field(
        description="A paragraph or two on how the market looks for this holding period, "
        "from the candidates and the market block."
    )
    approach: str = Field(
        description="A paragraph on how the picks were chosen for this period and risk "
        "tolerance, the assumptions made, and what the picks have in common."
    )
    picks: list[Pick] = Field(description="Five to ten picks, best first; weights sum to 1.")
    passed_over: list[str] = Field(
        description="Candidates a reader might have expected, each with why it was left out."
    )
    considerations: list[str] = Field(
        description="What to weigh before buying: taxes, trading costs, timing, "
        "concentration, and what would change the picks."
    )
    data_limitations: list[str] = Field(description="What the provided data cannot show.")


class PicksResponse(BaseModel):
    """A list of Claude's picks together with the data it was written from."""

    id: UUID | None = Field(
        default=None, description="ID of the stored picks; null if they could not be saved."
    )
    report: PicksReport
    securities: list[ScreenedStock] = Field(
        default_factory=list,
        description="The screener's figures for each pick when the picks were made, in the "
        "picks' order; a pick the screener does not hold is left out.",
    )
    context: PicksContext
    model: str = Field(description="Claude model that made the picks.")
    generated_at: datetime
    cached: bool = Field(
        default=False,
        description="Whether these picks were made earlier, at ``generated_at``, and reused.",
    )
    disclaimer: str = (
        "Generated by an AI model (Claude), not a licensed financial adviser, from the "
        "screener's historical figures and what you told it. No pick is a promise: any of "
        "them can lose money, and shorter holding periods are the least predictable. Check "
        "prices and news before buying; the decision and its risk are yours."
    )


class PicksSummary(BaseModel):
    """A stored list of picks, without its reasons or data."""

    id: UUID
    generated_at: datetime
    horizon: str = Field(description="The holding period in words.")
    risk_tolerance: str | None = None
    model: str
    headline: str
    symbols: list[str] = Field(description="The picks' tickers, best first.")


class PicksList(BaseModel):
    """An account's stored lists of picks, newest first."""

    items: list[PicksSummary]


class ReviewedPick(Pick):
    """One of Claude's picks as a portfolio review is given it, with the screener's
    figures for it when it was picked."""

    metrics: ScreenedStock | None = Field(
        default=None,
        description="The screener's figures for the pick at the time; null when the "
        "screener did not hold it.",
    )


class PicksForReview(BaseModel):
    """The account's latest Claude's picks, given to a portfolio review as candidates for
    new investments."""

    generated_at: datetime
    profile: PicksProfile = Field(description="What the picks were tailored to.")
    horizon: str = Field(description="The holding period they were made for, in words.")
    prices_as_of: date | None = Field(
        default=None, description="Date of the screener's closes the picks were made from."
    )
    headline: str
    market_view: str
    approach: str
    picks: list[ReviewedPick]
