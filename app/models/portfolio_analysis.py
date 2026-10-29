"""Schemas for Claude's review of the account's saved portfolio."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.portfolio import MAX_SYMBOLS, PortfolioNumber, PortfolioPeriod
from app.models.research import RiskFactor


class InvestorProfile(BaseModel):
    """What the review should tailor its plan to; every field is optional."""

    risk_tolerance: Literal["conservative", "moderate", "aggressive"] | None = None
    horizon_years: int | None = Field(
        default=None, ge=0, le=60, description="Years until the money is needed."
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


class PortfolioAnalysisRequest(BaseModel):
    """Options for a review of one of the saved portfolios."""

    portfolio: PortfolioNumber = Field(default=1, description="Which saved portfolio to review.")
    profile: InvestorProfile = Field(default_factory=InvestorProfile)
    period: PortfolioPeriod = Field(
        default="1y", description="Window of daily returns the statistics are computed on."
    )
    refresh: bool = Field(
        default=False,
        description="Write a new review even when one for the same holdings and period "
        "is recent enough to reuse.",
    )


class AssetRisk(BaseModel):
    """One holding: its place in the portfolio and its statistics over the window.

    Returns and volatilities are decimals (0.25 = 25%), annualized from daily returns.
    """

    symbol: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    shares: float = Field(gt=0, allow_inf_nan=False)
    price: float = Field(gt=0, allow_inf_nan=False, description="Latest price.")
    value: float = Field(gt=0, allow_inf_nan=False, description="Shares times the latest price.")
    weight: float = Field(ge=0, le=1, description="Share of the portfolio's value.")
    annualized_return: float
    volatility: float
    max_drawdown: float = Field(le=0)
    risk_contribution: float = Field(
        description="Share of the portfolio's variance this holding accounts for; the "
        "shares sum to 1, and one above the holding's weight means it adds more risk "
        "than capital."
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
    average_correlation: float | None = Field(
        default=None,
        description="Average correlation over every pair of holdings, equally weighted; "
        "null for a portfolio of one holding.",
    )


class PortfolioContext(BaseModel):
    """The data a portfolio review is written from, sent to Claude verbatim."""

    profile: InvestorProfile = Field(default_factory=InvestorProfile)
    period: PortfolioPeriod
    total_value: float = Field(
        gt=0, allow_inf_nan=False, description="Value of the priced holdings, at latest prices."
    )
    holdings: list[AssetRisk] = Field(min_length=1, max_length=MAX_SYMBOLS)
    sectors: dict[str, float] = Field(
        description="Weight in each sector, by the providers' classification; holdings "
        "without one are under Unclassified."
    )
    portfolio: PortfolioRiskStats
    most_correlated: list[PairCorrelation] = Field(default_factory=list)
    least_correlated: list[PairCorrelation] = Field(default_factory=list)
    left_out: list[str] = Field(
        default_factory=list,
        description="Holdings left out of the review because they could not be priced or "
        "have too little history; ``notice`` says which and why.",
    )
    notice: str | None = Field(
        default=None, description="Explains anything left out of the window or the holdings."
    )


OrderType = Literal["market", "limit"]


class TargetPosition(BaseModel):
    """Where Claude would take one position: a current holding or a new investment."""

    symbol: str = Field(description="Ticker, e.g. VXUS; CASH for uninvested cash.")
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
        "a holding to sell entirely gets 0. Weights sum to 1."
    )
    considerations: list[str] = Field(
        description="Trade-offs of the plan and alternatives worth weighing: taxes, "
        "trading costs, timing, and what would change the recommendation."
    )
    data_limitations: list[str] = Field(description="What the provided data cannot show.")


class PlannedTrade(BaseModel):
    """One order that carries out the plan, computed from the targets and latest prices."""

    symbol: str
    action: Literal["buy", "sell"]
    shares: float | None = Field(
        description="Whole shares to trade; null when the symbol could not be priced."
    )
    price: float | None = Field(description="Latest price the trade was sized at.")
    value: float = Field(description="Approximate value of the trade.")
    order_type: OrderType
    limit_price: float | None = None
    rationale: str


class RebalancePlan(BaseModel):
    """The orders that move the portfolio to Claude's target allocation."""

    total_value: float = Field(description="Value of the holdings plus the new cash.")
    trades: list[PlannedTrade]
    cash_after: float = Field(description="Cash left over, including any CASH target.")
    notes: list[str] = Field(default_factory=list)


class PortfolioAnalysisResponse(BaseModel):
    """A portfolio review together with the data it was written from."""

    id: UUID | None = Field(
        default=None, description="ID of the stored review; null if it could not be saved."
    )
    period: PortfolioPeriod
    report: PortfolioReview
    plan: RebalancePlan | None = Field(
        default=None, description="Orders computed from the review's target allocation."
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
        "prices of today's holdings and what you told it. Check prices, taxes and trading "
        "costs before placing any order; the decision and its risk are yours."
    )
