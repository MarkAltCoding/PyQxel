"""Schemas for Claude's Picks: the securities Claude would buy now and hold for a period
the person chooses, picked from the screener's universe.

:class:`PicksReport` doubles as the structured-output schema Claude fills in, so it uses
only plain types and literals.
"""

from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.core.text import listed
from app.models.markets import MarketContext
from app.models.news import NewsHeadline
from app.models.nightly import CUSTOM_SYMBOL_PATTERN, NightlyRefreshSettings
from app.models.research import (
    EXTRA_CONTEXT_DESCRIPTION,
    MAX_EXTRA_CONTEXT,
    MAX_WAIT_SECONDS,
    WAIT_DESCRIPTION,
    WRITTEN_BY_DESCRIPTION,
)
from app.models.schedule import TIME_PATTERN
from app.models.screener import AssetType, ScreenedStock

# Re-exported, since much of the app reads them from here.
from app.models.security_facts import CandidateEvents as CandidateEvents
from app.models.security_facts import CandidateFiling as CandidateFiling  # noqa: F401
from app.models.security_facts import ScreenerFacts as ScreenerFacts
from app.models.security_facts import SectorSnapshot as SectorSnapshot
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

MAX_CANDIDATES: int = 640
"""Most candidates the screener offers Claude for one list of picks: room for every lens's
leaders, so the lenses listed last (the ETFs') are never crowded out."""

MAX_ADDED_SYMBOLS: int = 25
"""Most tickers a person may add to the screener's candidates for one list of picks."""

MAX_CANDIDATE_EVENTS: int = 100
"""Most candidates or holdings whose own news and SEC filings are given to Claude, when
the person waits long enough for them all."""


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
        "then given in dollars too. Without one, the picks are sized in percentages alone, "
        "as if there were enough money to buy any share of each.",
    )
    weights: Literal["ideal", "clean"] = Field(
        default="ideal",
        description="``ideal`` for each pick's best share, however precise (23%), or "
        "``clean`` for whole multiples of 5% (25%), easier to follow by hand.",
    )
    asset_types: list[AssetType] = Field(
        default_factory=list,
        max_length=2,
        description="Pick only stocks, only ETFs, or either when empty.",
    )
    sell_at_end: bool = Field(
        default=True,
        description="Whether everything is sold by the end of the holding period. With "
        "``false``, the picks are still chosen for the holding period, but its end is when "
        "they are next looked at: Claude says to sell then only where that is best, or "
        "when another reason to sell is met, and otherwise how long to keep holding, or "
        "until what.",
    )
    recurring: bool = Field(
        default=False,
        description="Whether new picks will be made each period, as a recurring "
        "investment. With ``sell_at_end`` false, Claude then writes a handoff for the next "
        "list: what to check for each pick it would keep.",
    )
    notes: str | None = Field(
        default=None,
        max_length=1_000,
        description="Anything else to take into account, e.g. sectors to favor or avoid, "
        "dividends wanted, or securities already held.",
    )


class PicksRequest(BaseModel):
    """Options for a list of Claude's Picks."""

    profile: PicksProfile = Field(default_factory=PicksProfile)
    web_search: bool = Field(
        default=False,
        description="Let Claude search the web first, for news and the backdrop the screener "
        "cannot show. Every fact it brings back is listed with its source. Anthropic bills "
        "each search to your key, on top of the tokens.",
    )
    wait_seconds: int = Field(default=0, ge=0, le=MAX_WAIT_SECONDS, description=WAIT_DESCRIPTION)
    holdings_portfolios: list[int] = Field(
        default_factory=list,
        max_length=5,
        description="Saved portfolios whose holdings Claude is shown, so it picks with what "
        "you already own in mind; empty for none. They come with what PyQxel already knows "
        "of them (the screener's figures), at no extra request.",
    )
    added_symbols: list[Annotated[str, StringConstraints(pattern=CUSTOM_SYMBOL_PATTERN)]] = Field(
        default_factory=list,
        max_length=MAX_ADDED_SYMBOLS,
        description="Tickers to weigh along with the screener's leaders, such as ones "
        "in a theme you follow. Each one the screener holds joins the candidates with "
        "its figures, as any other candidate does; one it does not hold is left out.",
    )
    extra_context: str | None = Field(
        default=None, max_length=MAX_EXTRA_CONTEXT, description=EXTRA_CONTEXT_DESCRIPTION
    )
    added_first: bool = Field(
        default=True,
        description="Whether the tickers added come first in the order the wait is spent "
        "(their news, articles, and signals), ahead of the 25 candidates likeliest to be "
        "picked; ``false`` puts those first and the added tickers after them.",
    )
    refresh: bool = Field(
        default=False,
        description="Pick anew even when picks for the same profile and screener data are "
        "recent enough to reuse.",
    )

    @field_validator("added_symbols", mode="before")
    @classmethod
    def _upper_symbols(cls, value: object) -> object:
        if isinstance(value, list):
            cleaned = [item.strip().upper() if isinstance(item, str) else item for item in value]
            return list(dict.fromkeys(cleaned))
        return value


class WeeklyPicksTime(BaseModel):
    """When weekly picks are made: a day of the week and a local time."""

    weekday: int = Field(default=6, ge=0, le=6, description="Monday 0 to Sunday 6.")
    time: str = Field(default="09:00", pattern=TIME_PATTERN, description="HH:MM, 24-hour.")


class MonthlyPicksTime(BaseModel):
    """When monthly picks are made: a day of the month and a local time."""

    month_day: int = Field(
        default=-1,
        ge=-1,
        le=31,
        description="1 to 31 (a shorter month's last day for 29 to 31), or -1 for the last "
        "day of every month.",
    )
    time: str = Field(default="09:00", pattern=TIME_PATTERN, description="HH:MM, 24-hour.")


class PicksScheduleSettings(BaseModel):
    """Makes Claude's Picks on a schedule, for a week's or a month's holding period.

    The keys the request carries (Anthropic's and the market data keys) are encrypted and
    stored with it, replacing any stored earlier, since the picks are made with no browser
    open; turning both schedules off deletes them.
    """

    weekly: WeeklyPicksTime | None = Field(
        default=None, description="Picks to hold for a week; null for none."
    )
    monthly: MonthlyPicksTime | None = Field(
        default=None, description="Picks to hold for a month; null for none."
    )
    time_zone: str = Field(default="America/New_York", max_length=64)
    request: PicksRequest = Field(
        default_factory=PicksRequest,
        description="How the picks are made: the profile (its horizon is each schedule's), "
        "web search, waiting for data, and the portfolios whose holdings are shown.",
    )

    @model_validator(mode="after")
    def _a_known_time_zone(self) -> Self:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown time zone {self.time_zone!r}.") from exc
        return self


class PicksScheduleStatus(BaseModel):
    """Whether Claude's Picks are made on a schedule, when next, and how the last went."""

    available: bool = Field(description="Whether this server can store keys to run them.")
    enabled: bool = Field(description="Whether either schedule is on.")
    weekly: WeeklyPicksTime | None = None
    monthly: MonthlyPicksTime | None = None
    time_zone: str | None = None
    request: PicksRequest | None = None
    providers: list[str] = Field(default_factory=list, description="The keys stored.")
    next_weekly_at: datetime | None = None
    next_monthly_at: datetime | None = None
    weekly_ran_at: datetime | None = None
    monthly_ran_at: datetime | None = None
    last_error: str | None = Field(
        default=None, description="Why the last scheduled run made no picks; null if it did."
    )


class PickCandidate(ScreenerFacts):
    """A security the screener offered Claude, with the measures it led on."""

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


class PicksCandidateRow(BaseModel):
    """One security the next list of picks would weigh."""

    symbol: str
    name: str
    asset_type: AssetType = "stock"
    sector: str | None = None
    lenses: list[str] = Field(description="The measures it led the screener on.")
    added: bool = Field(description="Whether it is one of the tickers you added.")


class PicksCandidateList(BaseModel):
    """The securities the next list of picks would weigh, for the settings given."""

    candidates: list[PicksCandidateRow] = Field(
        description="The screener's leaders, then the tickers added that it did not "
        "already offer."
    )
    missing: list[str] = Field(
        default_factory=list,
        description="Tickers added that the screener does not hold, so they are left out.",
    )
    own: bool = Field(description="Whether the screener is your own, built by your refresh.")


class PicksHolding(BaseModel):
    """A security the person already holds, across the portfolios they chose to show."""

    symbol: str
    shares: float = Field(gt=0)
    accounts: list[str] = Field(description="Each portfolio holding it, with its shares.")
    value: float | None = Field(
        default=None, description="Shares times the screener's price; null when unpriced."
    )
    weight: float | None = Field(
        default=None, description="Its share of the priced holdings shown, as a decimal."
    )


class PicksContext(BaseModel):
    """The data a list of picks is written from. All of it is sent to Claude, the
    candidates as a table."""

    profile: PicksProfile
    horizon: str = Field(description="The holding period in words.")
    horizon_weeks: float = Field(gt=0, description="The holding period in weeks.")
    today: date
    holding_from: datetime | None = Field(
        default=None,
        description="When the holding begins, New York time: now while the NYSE is open, "
        "else the next session's open.",
    )
    holding_until: datetime | None = Field(
        default=None,
        description="When it ends, New York time: the close of the period's last trading "
        "session (a 1 p.m. early close on some days before holidays). A week chosen over a "
        "weekend runs from Monday's open to Friday's close.",
    )
    universe: PicksUniverse
    candidates: list[PickCandidate] = Field(max_length=MAX_CANDIDATES + MAX_ADDED_SYMBOLS)
    market: MarketContext | None = Field(
        default=None,
        description="The market backdrop: upcoming FOMC meetings, market holidays, major "
        "companies' earnings reports, and IPOs, and the newest market headlines.",
    )
    deal_headlines: list[NewsHeadline] = Field(
        default_factory=list,
        description="The headlines on mergers and acquisitions that look most "
        "market-moving, newest first.",
    )
    forex_headlines: list[NewsHeadline] = Field(
        default_factory=list,
        description="The same of the currency news: the dollar and exchange rates.",
    )
    crypto_headlines: list[NewsHeadline] = Field(
        default_factory=list, description="The same of the cryptocurrency news."
    )
    sectors: list[SectorSnapshot] = Field(
        default_factory=list,
        description="The whole screener's stocks, then each sector's, as medians: what a "
        "candidate's figures compare against.",
    )
    candidate_events: list[CandidateEvents] = Field(
        default_factory=list,
        max_length=MAX_CANDIDATE_EVENTS,
        description="Recent news and SEC filings of the candidates likeliest to be picked: "
        "the stocks that led on the most measures, then the largest.",
    )
    holdings: list[PicksHolding] = Field(
        default_factory=list,
        description="What the person already holds in the portfolios they chose to show, "
        "largest first; empty when they chose none.",
    )
    holding_figures: list[ScreenerFacts] = Field(
        default_factory=list,
        description="The screener's figures for those holdings it holds, sent to Claude "
        "as a table.",
    )
    web_research: WebResearch | None = Field(
        default=None,
        description="What a web search made for these picks found, each fact with its "
        "sources; null when the web was not searched.",
    )
    additional_context: str | None = Field(
        default=None, description="The person's additional context. " + EXTRA_CONTEXT_DESCRIPTION
    )
    notice: str | None = Field(default=None, description="Anything missing from the data.")


class PickOrder(BaseModel):
    """How to buy one pick at a typical US broker, and how to guard it once bought.

    Every field maps to a choice on a broker's order ticket: the order type, its prices,
    and how long it stays open (its time in force). The app turns it into the steps to
    follow, and measures the pick's performance by whether, when, and at what price the
    order would have filled.
    """

    order_type: Literal["market", "limit", "stop_limit"] = Field(
        description="``market`` buys at the next price available; ``limit`` buys only at "
        "limit_price or lower; ``stop_limit`` waits until the price rises to stop_price, "
        "then buys at limit_price or lower (to buy a breakout without chasing it)."
    )
    limit_price: float | None = Field(
        default=None,
        description="The most to pay per share, in the security's currency; required for "
        "limit and stop_limit orders, null for market orders.",
    )
    stop_price: float | None = Field(
        default=None,
        description="For a stop_limit order, the price that sets it off; at or below "
        "limit_price. Null otherwise.",
    )
    time_in_force: Literal["day", "gtc"] = Field(
        description="``day``: canceled at the close if not filled; ``gtc``: good 'til "
        "canceled, left open for good_for_days trading days."
    )
    good_for_days: int | None = Field(
        default=None,
        description="For a gtc order, how many trading sessions to leave it open before "
        "canceling it, at most the holding period's; null for a day order.",
    )
    if_unfilled: Literal["skip", "buy_at_market"] = Field(
        description="The contingency when the order never fills: ``skip`` keeps the money "
        "in cash; ``buy_at_market`` buys at the next session's open after it is canceled."
    )
    stop_loss: float | None = Field(
        default=None,
        description="Once bought, a sell stop order at this price, below the purchase, to "
        "limit the loss; null for none.",
    )
    take_profit: float | None = Field(
        default=None,
        description="Once bought, a sell limit order at this price, above the purchase, to "
        "take the gain; null for none.",
    )
    cancel_after: date | None = Field(
        default=None,
        description="The last session the buy order stays open, New York date; set by the "
        "app from time_in_force and good_for_days.",
    )


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
    order: PickOrder | None = Field(
        default=None,
        description="The buy order to place, with its contingency, stop-loss, and profit "
        "target; null on lists made before orders were given.",
    )
    sell_at_end: bool = Field(
        default=True,
        description="Whether to sell it by the end of the holding period. False only when "
        "the profile's sell_at_end is false and holding on is better; exit then says for "
        "how long, or until what.",
    )


class PicksReport(BaseModel):
    """Claude's Picks for a holding period, ranked, with how they were chosen."""

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
    handoff: str | None = Field(
        default=None,
        description="For a recurring investment that keeps picks past the period: a note "
        "for the next list of picks, to be pasted into its additional context. Null "
        "otherwise.",
    )
    written_by: str | None = Field(default=None, description=WRITTEN_BY_DESCRIPTION)


class PicksResponse(BaseModel):
    """A list of Claude's Picks together with the data it was written from."""

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
    effort: str | None = Field(
        default=None,
        description="How much effort Claude was asked to put in (low, medium, high, xhigh, "
        "or max); null when it was written outside the app.",
    )
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
    effort: str | None = Field(
        default=None,
        description="How much effort Claude was asked to put in (low, medium, high, xhigh, "
        "or max); null when it was written outside the app.",
    )
    headline: str
    symbols: list[str] = Field(description="The picks' tickers, best first.")


class PicksList(BaseModel):
    """An account's stored lists of picks, newest first."""

    items: list[PicksSummary]


class ReviewedPick(Pick):
    """One of Claude's Picks as a portfolio review is given it, with the screener's
    figures for it when it was picked."""

    metrics: ScreenedStock | None = Field(
        default=None,
        description="The screener's figures for the pick at the time; null when the "
        "screener did not hold it.",
    )


class PicksForReview(BaseModel):
    """The account's latest Claude's Picks, given to a portfolio review as candidates for
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


PicksBenchmark = Literal["SPY", "QQQ", "DIA", "IWM", "VTI"]
"""What a list of picks is compared with: a fund tracking a market index, which every
price key covers and whose adjusted closes count dividends, as an index's level does not."""

BENCHMARK_NAMES: dict[str, str] = {
    "SPY": "S&P 500 (SPY)",
    "QQQ": "Nasdaq-100 (QQQ)",
    "DIA": "Dow Jones Industrial Average (DIA)",
    "IWM": "Russell 2000 small caps (IWM)",
    "VTI": "US total stock market (VTI)",
}
"""Each benchmark's name as the app shows it."""


class PickPerformance(BaseModel):
    """How one pick has done since the list was made."""

    symbol: str
    weight: float = Field(ge=0, le=1, description="Its share of the money at the start.")
    start_price: float | None = Field(
        default=None, description="Its adjusted close the list is measured from."
    )
    latest_price: float | None = Field(
        default=None, description="Its latest adjusted close, or the last in the period."
    )
    gain: float | None = Field(
        default=None,
        description="Its gain or loss since the start, as a decimal, dividends counted; "
        "null when it could not be priced.",
    )
    contribution: float | None = Field(
        default=None,
        description="What it added to the list's return: its weight times its gain.",
    )
    status: Literal["held", "waiting", "not_filled", "sold", "unpriced"] = Field(
        default="held",
        description="``held``: bought and still held, or held to the period's end; "
        "``waiting``: its buy order is still open, unfilled; ``not_filled``: the order "
        "never filled and the money stayed in cash; ``sold``: its stop-loss or profit "
        "target sold it; ``unpriced``: no prices.",
    )
    filled_on: date | None = Field(default=None, description="The day its buy order filled.")
    sold_on: date | None = Field(
        default=None, description="The day its stop-loss or profit target sold it."
    )
    sold_by: Literal["stop_loss", "take_profit"] | None = Field(
        default=None, description="Which order sold it."
    )
    note: str | None = Field(
        default=None, description="What happened to its orders, in a sentence."
    )


class PerformancePoint(BaseModel):
    """The list's and the benchmark's gain since the start, on one trading day."""

    date: date
    picks: float | None = None
    benchmark: float | None = None


class PicksPerformance(BaseModel):
    """How a list of picks has done since it was made, held as Claude weighted it and
    never rebalanced, against a benchmark, until its holding period ends."""

    id: UUID
    benchmark: PicksBenchmark
    benchmark_name: str
    start: date | None = Field(
        default=None,
        description="The trading day whose close the list is measured from: the last close "
        "before the picks were made.",
    )
    as_of: date | None = Field(
        default=None,
        description="The last trading day measured: today's latest, or the "
        "last of the holding period once it has ended.",
    )
    ends_on: date = Field(description="The day the holding period ends; tracking stops then.")
    ended: bool = Field(description="Whether the holding period is over.")
    total_return: float | None = Field(
        default=None,
        description="The list's gain or loss since the start, as a decimal: each pick's gain "
        "times its weight, among the picks that could be priced.",
    )
    benchmark_return: float | None = None
    excess_return: float | None = Field(
        default=None, description="The list's return minus the benchmark's."
    )
    picks: list[PickPerformance] = Field(default_factory=list)
    series: list[PerformancePoint] = Field(
        default_factory=list, description="Each trading day's gain since the start."
    )
    notice: str | None = Field(default=None, description="What could not be priced, if any.")


class SavedPicks(PicksSummary):
    """A stored list of picks with how it has done, for the list of saved picks."""

    ends_on: date
    ended: bool
    total_return: float | None = None
    benchmark_return: float | None = None
    excess_return: float | None = None


class SavedPicksList(BaseModel):
    """An account's stored lists of picks, newest first, each with how it has done."""

    benchmark: PicksBenchmark
    benchmark_name: str
    items: list[SavedPicks]
    notice: str | None = Field(
        default=None, description="Why some lists have no performance, if any."
    )


class ScheduleConflictCheck(BaseModel):
    """A schedule about to be saved, checked against the other kind: the screener refresh
    against the scheduled picks, or the picks against the refresh. A side left out is
    taken as stored."""

    nightly: NightlyRefreshSettings | None = Field(
        default=None, description="The screener refresh's schedule about to be saved."
    )
    picks: PicksScheduleSettings | None = Field(
        default=None, description="The scheduled picks about to be saved."
    )


class ScheduleConflicts(BaseModel):
    """Where scheduled picks would fall inside a screener refresh."""

    warnings: list[str] = Field(
        description="Each picks run that would start while the refresh runs, in words; "
        "such a run waits for the refresh to finish. Empty when none would."
    )


WAIT_CHOICES: tuple[int, ...] = (0, 60, 120, 300, 600, 900, 1200, 1800)
"""The waits offered, in seconds, as the web app's menu lists them."""


class WaitGroupPlan(BaseModel):
    """What one group of securities gets within a wait: the holdings, the candidates
    likeliest to be picked, or the rest."""

    name: str = Field(description="The group, in words, e.g. Your holdings.")
    stocks: int = Field(ge=0, description="Stocks in the group whose own data is wanted.")
    with_news: int = Field(ge=0, description="Of them, those whose news is fetched.")
    with_signals: int = Field(
        ge=0, description="Of them, those that get analysts' ratings, earnings, and insiders."
    )
    articles_each: int = Field(
        ge=0, description="Full articles read for each of those with news, at most."
    )
    headlines_each: int = Field(ge=0, description="Headlines given for each, at most.")


class WaitPlan(BaseModel):
    """What one wait buys: how the time is split among the securities, by priority."""

    wait_seconds: int = Field(ge=0)
    minutes: int = Field(ge=1, description="Minutes of the Finnhub plan's limit it spans.")
    groups: list[WaitGroupPlan]
    articles: int = Field(ge=0, description="Company articles read in full, at most.")
    headlines: int = Field(ge=0, description="Company headlines given, at most.")
    market_articles: int = Field(ge=0, description="Market and deal stories read in full.")
    market_headlines: int = Field(
        ge=0, description="Market, deal, currency, and crypto headlines given, at most."
    )
    calendar_days: int = Field(ge=0, description="Days of earnings calendar it covers.")
    full_calendar_days: int = Field(ge=0, description="Days the whole calendar would cover.")
    complete: bool = Field(description="Whether nothing would be left out for lack of time.")
    more: str | None = Field(default=None, description="What a longer wait would add.")


class WaitPlans(BaseModel):
    """What each wait on offer would gather for a report, list of picks, or review."""

    per_minute: int | None = Field(
        description="Requests a minute the Finnhub key allows; null without one, when news "
        "comes from Alpha Vantage's shared feed and waiting adds nothing."
    )
    choices: list[WaitPlan]
