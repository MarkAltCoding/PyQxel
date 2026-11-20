"""Schemas for Claude's review of the account's saved portfolio."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.models.accounts import AccountType, ContributionLimits

from app.models.markets import MarketContext
from app.models.news import NewsHeadline
from app.models.picks import (
    MAX_CANDIDATE_EVENTS,
    CandidateEvents,
    PicksForReview,
    ScreenerFacts,
    SectorSnapshot,
)
from app.models.portfolio import MAX_PORTFOLIOS, MAX_SYMBOLS, PortfolioNumber, PortfolioPeriod
from app.models.research import (
    MAX_WAIT_SECONDS,
    WAIT_DESCRIPTION,
    WRITTEN_BY_DESCRIPTION,
    RiskFactor,
)
from app.models.stock import FundProfile
from app.models.web import WebResearch


class InvestorProfile(BaseModel):
    """What the review should tailor its plan to; every field is optional."""

    risk_tolerance: Literal["conservative", "moderate", "aggressive"] | None = None
    horizon_years: float | None = Field(
        default=None,
        ge=0,
        le=60,
        allow_inf_nan=False,
        description="Years until the money is needed; a fraction for a horizon in months, "
        "such as 0.5 for six months.",
    )
    cash_to_invest: float = Field(
        default=0.0,
        ge=0,
        le=1e9,
        allow_inf_nan=False,
        description="New cash to put to work, in the holdings' currency.",
    )
    notes: str | None = Field(
        default=None,
        max_length=1_000,
        description="Anything else to take into account, e.g. a taxable account, holdings "
        "to keep, or sectors to avoid.",
    )
    fractional_shares: bool = Field(
        default=True,
        description="Whether the person's broker trades fractions of a share: then every "
        "order is sized to the exact fraction its target needs; otherwise orders are in "
        "whole shares, and in fractions only where whole shares cannot carry them out.",
    )
    round_shares: bool = Field(
        default=True,
        description="With fractional shares, leave each holding at whole shares or a round "
        "fraction such as 3.5, rather than 3.438, where that keeps it within a quarter of a "
        "percent of the portfolio's value of its target.",
    )


MIN_HISTORY_MONTHS: int = 12
"""Shortest custom window a review's statistics are computed on: about 250 daily returns."""

MAX_HISTORY_MONTHS: int = 120
"""Longest custom window, ten years of daily returns."""

PERIOD_MONTHS: dict[PortfolioPeriod, int] = {"1y": 12, "2y": 24, "5y": 60, "10y": 120}
"""Months each standard window spans."""


GroupMode = Literal["combined", "separate"]
"""How several portfolios are reviewed at once: ``combined`` as one large portfolio, with
one plan saying which account holds what, or ``separate``, each with its own plan and
orders, written with the others in mind (one request, so cheaper than a review of each)."""


class AccountGoals(BaseModel):
    """What one portfolio's own plan is tailored to, in a ``separate`` review of several.
    A goal left null is the shared profile's."""

    portfolio: PortfolioNumber
    risk_tolerance: Literal["conservative", "moderate", "aggressive"] | None = None
    horizon_years: float | None = Field(
        default=None, ge=0, le=60, allow_inf_nan=False, description="Years until it is needed."
    )
    notes: str | None = Field(default=None, max_length=1_000)
    cash_available: float = Field(
        default=0.0,
        ge=0,
        le=1e9,
        allow_inf_nan=False,
        description="Cash already in this account that may be traded with, in dollars, "
        "besides any money-market fund it holds.",
    )


class PortfolioAnalysisRequest(BaseModel):
    """Options for a review of one of the saved portfolios, or of several at once."""

    portfolio: PortfolioNumber = Field(default=1, description="Which saved portfolio to review.")
    portfolios: list[PortfolioNumber] = Field(
        default_factory=list,
        max_length=MAX_PORTFOLIOS,
        description="Review these saved portfolios at once, in place of ``portfolio``: two "
        "to five of them, each named once. Empty for one portfolio's review.",
    )
    group_mode: GroupMode = Field(
        default="combined",
        description="With ``portfolios``: review them as one large portfolio "
        "(``combined``), or each with its own plan and orders, with the others in mind "
        "(``separate``).",
    )
    accounts: list[AccountGoals] = Field(
        default_factory=list,
        max_length=MAX_PORTFOLIOS,
        description="In a ``separate`` review: each portfolio's own goals, where they "
        "differ from ``profile``'s, and the cash in it to trade with. ``profile``'s "
        "``cash_to_invest`` is then new money any of them may draw on.",
    )
    profile: InvestorProfile = Field(default_factory=InvestorProfile)
    period: PortfolioPeriod = Field(
        default="1y", description="Window of daily returns the statistics are computed on."
    )
    history_months: int | None = Field(
        default=None,
        ge=MIN_HISTORY_MONTHS,
        le=MAX_HISTORY_MONTHS,
        description="A custom window of daily returns, in months, used instead of ``period``.",
    )
    web_search: bool = Field(
        default=False,
        description="Let Claude search the web first, for news on the holdings and the "
        "backdrop, which the price statistics cannot show. Every fact it brings back is "
        "listed with its source. Anthropic bills each search to your key, on top of the "
        "tokens.",
    )
    wait_seconds: int = Field(default=0, ge=0, le=MAX_WAIT_SECONDS, description=WAIT_DESCRIPTION)
    refresh: bool = Field(
        default=False,
        description="Write a new review even when one for the same holdings and period "
        "is recent enough to reuse.",
    )

    @model_validator(mode="after")
    def _a_group_of_two_or_more(self) -> Self:
        if len(set(self.portfolios)) != len(self.portfolios):
            raise ValueError("Name each portfolio once.")
        if len(self.portfolios) == 1:
            raise ValueError("Review two or more portfolios together, or one on its own.")
        named = {account.portfolio for account in self.accounts}
        if len(named) != len(self.accounts) or not named <= set(self.portfolios):
            raise ValueError("Give goals once for each portfolio in the review.")
        return self

    @property
    def group(self) -> list[int]:
        """The portfolios reviewed together, in order; empty for one portfolio's review."""
        return sorted(self.portfolios)

    @property
    def separate(self) -> bool:
        """Whether each portfolio of a group gets its own plan."""
        return bool(self.portfolios) and self.group_mode == "separate"

    @property
    def window(self) -> PortfolioPeriod:
        """The standard window the prices are fetched over: ``period``, or the shortest
        that covers ``history_months``, which the returns are then trimmed to."""
        if self.history_months is None:
            return self.period
        return next(p for p, months in PERIOD_MONTHS.items() if self.history_months <= months)


class AccountShares(BaseModel):
    """How many shares of a holding one account holds, in a review of several accounts."""

    account: str = Field(description="The account's name, as the person saved it.")
    shares: float = Field(gt=0, allow_inf_nan=False)


class AccountValue(BaseModel):
    """One of the accounts reviewed, with its value at the latest prices, what kind of
    account it is, and, in a ``separate`` review, its own goals and cash."""

    account: str = Field(description="The account's name, as the person saved it.")
    portfolio: int | None = Field(default=None, description="Which saved portfolio it is.")
    account_type: AccountType | None = Field(
        default=None, description="What kind of account it is; null when not said."
    )
    account_type_name: str | None = None
    tax_treatment: str | None = Field(
        default=None, description="How its gains are taxed, in a few words."
    )
    contribution_room: float | None = Field(
        default=None,
        description="The most new money that may still go into it this tax year, in "
        "dollars, as the person entered it; null when not said.",
    )
    cash_available: float = Field(
        default=0.0, description="Cash in it to trade with, besides money-market funds."
    )
    risk_tolerance: str | None = Field(
        default=None, description="Its own risk tolerance; null to use the profile's."
    )
    horizon_years: float | None = Field(
        default=None, description="Its own horizon; null to use the profile's."
    )
    notes: str | None = Field(default=None, description="What the person said about it.")
    value: float = Field(ge=0, allow_inf_nan=False)
    holdings: int = Field(ge=0, description="How many holdings it has.")


class AssetRisk(BaseModel):
    """One holding: its place in the portfolio and its statistics over the window.

    Returns and volatilities are decimals (0.25 = 25%), annualized from daily returns.
    Every holding is listed; one without enough prices for the statistics has them null,
    and ``data_note`` says why.
    """

    symbol: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    shares: float = Field(gt=0, allow_inf_nan=False)
    price: float | None = Field(
        default=None, gt=0, allow_inf_nan=False, description="Latest price; null if unknown."
    )
    value: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description="Shares times the latest price; null when it could not be priced.",
    )
    weight: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Share of the portfolio's value; null when it could not be priced.",
    )
    annualized_return: float | None = None
    volatility: float | None = None
    max_drawdown: float | None = Field(default=None, le=0)
    risk_contribution: float | None = Field(
        default=None,
        description="Share of the portfolio's variance this holding accounts for; the "
        "shares sum to 1 over the holdings with statistics, and one above the holding's "
        "weight means it adds more risk than capital.",
    )
    correlation_to_rest: float | None = Field(
        default=None,
        description="Correlation of its daily returns with the rest of the portfolio's; "
        "null for a portfolio of one holding, and for cash.",
    )
    cash: bool = Field(
        default=False,
        description="A money-market fund, counted as cash: its price stays at $1, so its "
        "statistics are zero.",
    )
    accounts: list[AccountShares] = Field(
        default_factory=list,
        description="In a review of several accounts together: the shares each account "
        "holds, which sum to ``shares``; empty when one portfolio is reviewed.",
    )
    data_note: str | None = Field(
        default=None,
        description="Why its statistics are missing, such as too little price history; "
        "null when it has them. It is still part of the portfolio.",
    )


class PairCorrelation(BaseModel):
    """Correlation of two holdings' daily returns over the window."""

    first: str
    second: str
    correlation: float = Field(ge=-1, le=1)


class PortfolioRiskStats(BaseModel):
    """The whole portfolio's statistics, holding today's weights over the window.

    The returns are those of the current weights rebalanced daily, not of the trades the
    account actually made.
    """

    start: datetime
    end: datetime
    observations: int = Field(ge=1, description="Number of daily returns.")
    annualized_return: float
    volatility: float
    max_drawdown: float = Field(le=0)
    current_drawdown: float = Field(le=0)
    return_to_volatility: float | None = Field(
        default=None, description="Annualized return over volatility; null at zero volatility."
    )
    diversification_ratio: float = Field(
        description="Weighted average of the holdings' volatilities over the portfolio's; "
        "1 means no diversification benefit, and higher is more."
    )
    effective_holdings: float = Field(
        description="1 / the sum of squared weights: how many equal positions the "
        "portfolio's concentration matches."
    )
    largest_weight: float
    covered_weight: float = Field(
        default=1.0,
        ge=0,
        le=1,
        description="Share of the portfolio's value these statistics cover: holdings "
        "without enough prices are left out of them, though not out of the review.",
    )
    average_correlation: float | None = Field(
        default=None,
        description="Average correlation over every pair of holdings, equally weighted; "
        "null for a portfolio of one holding.",
    )


class PortfolioContext(BaseModel):
    """The data a portfolio review is written from, sent to Claude verbatim."""

    profile: InvestorProfile = Field(default_factory=InvestorProfile)
    period: PortfolioPeriod
    history_months: int | None = Field(
        default=None,
        description="The custom window, in months, the statistics cover; null when they "
        "cover ``period``.",
    )
    total_value: float = Field(
        gt=0, allow_inf_nan=False, description="Value of the priced holdings, at latest prices."
    )
    holdings: list[AssetRisk] = Field(min_length=1, max_length=MAX_SYMBOLS * MAX_PORTFOLIOS)
    accounts: list[AccountValue] = Field(
        default_factory=list,
        description="The accounts reviewed together as one portfolio, with their values; "
        "empty when one portfolio is reviewed. Weights are shares of all of them combined.",
    )
    sectors: dict[str, float] = Field(
        description="Weight in each sector, by the providers' classification; a fund's "
        "stocks are spread over their sectors where they are known, and its bonds and cash "
        "count as Bonds and Cash; holdings without a sector are under Unclassified."
    )
    funds: list[FundProfile] = Field(
        default_factory=list,
        description="Each ETF or mutual fund among the holdings, largest first, with its "
        "largest holdings, mix of stocks, bonds, and cash, sectors, costs, and the figures "
        "weighted from the companies it holds, where they could be found.",
    )
    claude_picks: PicksForReview | None = Field(
        default=None,
        description="The account's latest Claude's picks, with the screener's figures for "
        "each when it was picked, as candidates for new investments; null when the account "
        "has none.",
    )
    web_research: WebResearch | None = Field(
        default=None,
        description="What a web search made for this review found, each fact with its "
        "sources; null when the web was not searched.",
    )
    holding_figures: list[ScreenerFacts] = Field(
        default_factory=list,
        max_length=MAX_SYMBOLS * MAX_PORTFOLIOS,
        description="The screener's figures for each holding it holds, largest first: "
        "valuation, margins, growth, momentum, risk, factor betas, where each falls in its "
        "sector, its next earnings date, and whether the S&P 500 holds it.",
    )
    sector_medians: list[SectorSnapshot] = Field(
        default_factory=list,
        description="The whole screener's stocks, then each of the holdings' sectors, as "
        "medians, to compare the holdings with.",
    )
    holding_events: list[CandidateEvents] = Field(
        default_factory=list,
        max_length=MAX_CANDIDATE_EVENTS,
        description="Recent news and SEC filings of the largest holdings that are not funds.",
    )
    market: MarketContext | None = Field(
        default=None,
        description="The market backdrop: upcoming FOMC meetings, holidays, major earnings "
        "reports, IPOs, and the week's market headlines.",
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
    group_mode: GroupMode | None = Field(
        default=None,
        description="For several accounts: reviewed as one (``combined``) or each with its "
        "own plan (``separate``); null for one portfolio's review.",
    )
    contribution_limits: ContributionLimits | None = Field(
        default=None,
        description="The IRS's yearly contribution limits, given when an account reviewed "
        "is one they cap.",
    )
    portfolio: PortfolioRiskStats
    most_correlated: list[PairCorrelation] = Field(default_factory=list)
    least_correlated: list[PairCorrelation] = Field(default_factory=list)
    left_out: list[str] = Field(
        default_factory=list,
        description="Holdings left out of the statistics because they could not be priced "
        "or have too little history; they are still in ``holdings`` and reviewed, and "
        "``notice`` says which and why.",
    )
    notice: str | None = Field(
        default=None, description="Explains anything left out of the window or the holdings."
    )


OrderType = Literal["market", "limit"]


class TargetPosition(BaseModel):
    """Where Claude would take one position: a current holding or a new investment."""

    symbol: str = Field(
        description="Ticker, e.g. VXUS. For cash, the money-market fund the portfolio "
        "holds it in, e.g. SPAXX, or Cash when it holds none; never CASH, which is a stock."
    )
    cash: bool = Field(
        default=False,
        description="True for cash: money left uninvested, or the money-market fund it sits "
        "in. Cash is never ordered; it is what the orders leave over.",
    )
    keep: bool = Field(
        default=False,
        description="True to leave a current holding exactly as it is, with no order; its "
        "target_weight is then taken as its current weight.",
    )
    target_weight: float = Field(
        ge=0, le=1, description="Share of the portfolio after the plan, as a decimal."
    )
    order_type: OrderType = Field(
        description="limit for less liquid securities or to control the price, market for "
        "liquid ones where filling matters more."
    )
    limit_offset_percent: float | None = Field(
        default=None,
        ge=-20,
        le=20,
        description="For limit orders: the limit as a percent from the latest price, "
        "negative below it (e.g. -0.5 buys up to 0.5% below the last price).",
    )
    rationale: str = Field(description="Why this weight, citing figures where it can.")


class AccountPlan(BaseModel):
    """One account's own plan, in a review of several accounts each with its own."""

    account: str = Field(description="The account's name, exactly as in the snapshot.")
    headline: str = Field(description="One sentence on this account and its plan.")
    plan_summary: str = Field(
        description="This account's plan in a paragraph: what to change and why, how it "
        "fits the other accounts, and what it is tailored to."
    )
    new_cash: float = Field(
        ge=0,
        description="How much of the profile's new cash to invest goes into this account, "
        "in dollars; the accounts' shares sum to at most that cash, and none exceeds the "
        "account's contribution_room when one is given.",
    )
    target_allocation: list[TargetPosition] = Field(
        description="Every holding of this account and any new investment in it with its "
        "target weight within the account; a holding to sell entirely gets 0. Weights sum "
        "to 1."
    )


class PortfolioReview(BaseModel):
    """Claude's data-grounded review of a portfolio, with a concrete plan to act on."""

    headline: str = Field(description="One-sentence assessment of the portfolio.")
    risk_level: Literal["low", "moderate", "elevated", "high"]
    diversification: Literal["poor", "fair", "good", "strong"]
    summary: str = Field(description="Two or three paragraphs on construction and risk.")
    strengths: list[str] = Field(description="What the portfolio does well, citing figures.")
    key_risks: list[RiskFactor]
    plan_summary: str = Field(
        description="The recommended plan in a paragraph: what to change and why, and the "
        "assumptions it rests on."
    )
    target_allocation: list[TargetPosition] = Field(
        description="Every current holding and any new investment with its target weight; "
        "a holding to sell entirely gets 0. Weights sum to 1. Empty when the review gives "
        "each account its own plan in account_plans."
    )
    account_plans: list[AccountPlan] = Field(
        default_factory=list,
        description="When the snapshot's group_mode is separate: one plan for each "
        "account, in the snapshot's order. Empty otherwise.",
    )
    considerations: list[str] = Field(
        description="Trade-offs of the plan and alternatives worth weighing: taxes, "
        "trading costs, timing, and what would change the recommendation."
    )
    data_limitations: list[str] = Field(description="What the provided data cannot show.")
    written_by: str | None = Field(default=None, description=WRITTEN_BY_DESCRIPTION)


class PlannedTrade(BaseModel):
    """One order that carries out the plan, computed from the targets and latest prices."""

    symbol: str
    action: Literal["buy", "sell"]
    shares: float | None = Field(
        description="Shares to trade: whole where they can be, fractional where whole shares "
        "would round the trade away or a fractional holding is sold in full; null when the "
        "symbol could not be priced."
    )
    price: float | None = Field(description="Latest price the trade was sized at.")
    value: float = Field(description="Approximate value of the trade.")
    order_type: OrderType
    limit_price: float | None = None
    rationale: str
    fractional: bool = Field(
        default=False,
        description="Whether ``shares`` is a fraction, which some brokers take only as a "
        "market or day limit order, and not as a stop or trailing stop.",
    )


class RebalancePlan(BaseModel):
    """The orders that move the portfolio to Claude's target allocation."""

    total_value: float = Field(description="Value of the holdings plus the new cash.")
    trades: list[PlannedTrade]
    cash_after: float = Field(description="Cash left over, including any cash target.")
    cash_symbol: str | None = Field(
        default=None,
        description="The money-market fund the cash sits in, e.g. SPAXX; null when the "
        "portfolio holds none.",
    )
    notes: list[str] = Field(default_factory=list)


class AccountOrders(BaseModel):
    """The orders that carry out one account's own plan."""

    account: str
    portfolio: int | None = None
    plan: RebalancePlan | None = None


class PortfolioAnalysisResponse(BaseModel):
    """A portfolio review together with the data it was written from."""

    id: UUID | None = Field(
        default=None, description="ID of the stored review; null if it could not be saved."
    )
    period: PortfolioPeriod
    history_months: int | None = Field(
        default=None, description="The custom window in months, when one was asked for."
    )
    report: PortfolioReview
    plan: RebalancePlan | None = Field(
        default=None, description="Orders computed from the review's target allocation."
    )
    account_orders: list[AccountOrders] = Field(
        default_factory=list,
        description="In a review giving each account its own plan: the orders for each.",
    )
    portfolios: list[int] = Field(
        default_factory=list,
        description="The portfolios reviewed together; empty for one portfolio's review.",
    )
    group_mode: GroupMode | None = Field(
        default=None, description="How they were reviewed together, if they were."
    )
    context: PortfolioContext
    model: str = Field(description="Claude model that wrote the review.")
    generated_at: datetime
    cached: bool = Field(
        default=False,
        description="Whether this review was written earlier, at ``generated_at``, and reused.",
    )
    disclaimer: str = (
        "Generated by an AI model (Claude), not a licensed financial adviser, from historical "
        "prices of today's holdings and what you told it. Check prices, taxes, and trading "
        "costs before placing any order; the decision and its risk are yours."
    )


class PortfolioReviewSummary(BaseModel):
    """A stored portfolio review, without its plan or data: a row of the saved reviews."""

    id: UUID
    generated_at: datetime
    portfolios: list[int] = Field(
        description="The portfolios it reviews: one, or several reviewed together."
    )
    group_mode: GroupMode | None = Field(
        default=None, description="How several were reviewed together; null for one."
    )
    model: str = Field(description="The Claude model that wrote it.")
    headline: str
    risk_tolerance: str | None = Field(default=None, description="The risk tolerance asked for.")
    horizon_years: float | None = Field(default=None, description="The horizon asked for.")
    notes: str | None = Field(default=None, description="The notes the person gave.")


class PortfolioReviewList(BaseModel):
    """An account's stored portfolio reviews, newest first."""

    items: list[PortfolioReviewSummary]
