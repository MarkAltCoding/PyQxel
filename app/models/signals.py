"""Schemas for what analysts, earnings and insiders say about a company, from Finnhub."""

from datetime import date
from datetime import date as Day

from pydantic import BaseModel, Field


class RecommendationMonth(BaseModel):
    """How many analysts rated the stock each way in one month."""

    period: date = Field(description="The month, as its first day.")
    strong_buy: int = Field(ge=0)
    buy: int = Field(ge=0)
    hold: int = Field(ge=0)
    sell: int = Field(ge=0)
    strong_sell: int = Field(ge=0)

    @property
    def total(self) -> int:
        """Analysts rating the stock that month."""
        return self.strong_buy + self.buy + self.hold + self.sell + self.strong_sell


class EarningsSurprise(BaseModel):
    """Reported earnings per share for one quarter against the analysts' estimate."""

    period: date = Field(description="The quarter's end.")
    year: int | None = None
    quarter: int | None = None
    actual: float | None = None
    estimate: float | None = None
    surprise: float | None = Field(default=None, description="Actual less estimate, per share.")
    surprise_percent: float | None = Field(
        default=None, description="The surprise as a decimal of the estimate (0.05 = 5%)."
    )


class EarningsDate(BaseModel):
    """A company's next scheduled earnings report."""

    date: Day
    hour: str | None = Field(
        default=None,
        description="``bmo`` before the market opens, ``amc`` after it closes, ``dmh`` "
        "during market hours; null when not announced.",
    )
    year: int | None = None
    quarter: int | None = None
    eps_estimate: float | None = None
    revenue_estimate: float | None = None


class InsiderTrade(BaseModel):
    """An open-market purchase or sale by a company insider, as filed on SEC Form 4."""

    name: str
    transaction_date: date
    filing_date: date | None = None
    code: str = Field(description="``P`` for a purchase, ``S`` for a sale.")
    shares: float = Field(description="Shares bought (positive) or sold (negative).")
    price: float | None = Field(default=None, description="Price per share.")
    value: float | None = Field(default=None, description="Shares times price, signed.")


class InsiderSentimentMonth(BaseModel):
    """Finnhub's monthly insider sentiment for a company."""

    month: date = Field(description="The month, as its first day.")
    mspr: float = Field(
        ge=-100,
        le=100,
        description="Monthly share purchase ratio: 100 when insiders only bought, -100 "
        "when they only sold.",
    )
    net_shares: float = Field(description="Shares insiders bought less shares they sold.")


class InsiderSummary(BaseModel):
    """Insiders' open-market trades over a window. Grants, option exercises and tax
    withholding are left out, since they say nothing about insiders' views."""

    start: date
    end: date
    purchases: int = Field(ge=0)
    sales: int = Field(ge=0)
    insiders_buying: int = Field(ge=0)
    insiders_selling: int = Field(ge=0)
    shares_bought: float = Field(ge=0)
    shares_sold: float = Field(ge=0)
    value_bought: float = Field(ge=0, description="Value of purchases with a price.")
    value_sold: float = Field(ge=0, description="Value of sales with a price.")
    net_value: float = Field(description="Value bought less value sold.")


class MarketSignals(BaseModel):
    """Analyst ratings, earnings results and dates, and insider trading for a company."""

    symbol: str
    recommendations: list[RecommendationMonth] = Field(
        description="Monthly analyst rating counts, newest first; empty without coverage."
    )
    earnings: list[EarningsSurprise] = Field(
        description="Recent quarters' reported against estimated EPS, newest first."
    )
    next_earnings: EarningsDate | None = Field(
        default=None,
        description="The next scheduled earnings report within three months, if announced.",
    )
    insiders: InsiderSummary | None = Field(
        default=None, description="Insiders' open-market trades; null when unavailable."
    )
    insider_trades: list[InsiderTrade] = Field(
        description="The newest open-market insider trades, newest first."
    )
    insider_sentiment: list[InsiderSentimentMonth] = Field(
        default_factory=list, description="Monthly insider sentiment for a year, newest first."
    )
    insider_window: tuple[date, date] | None = Field(
        default=None,
        exclude=True,
        description="The days insider trades were fetched for; null when they could not be.",
    )
    notice: str | None = Field(
        default=None, description="Says which parts Finnhub could not supply, if any."
    )


class SignalsContext(BaseModel):
    """Analyst ratings, earnings and insider trading as given to Claude."""

    recommendations: list[RecommendationMonth] = Field(
        description="The last few months of analyst rating counts, newest first."
    )
    earnings: list[EarningsSurprise]
    next_earnings: EarningsDate | None = None
    insiders: InsiderSummary | None = None
    insider_sentiment: list[InsiderSentimentMonth] = Field(
        default_factory=list, description="The last few months' insider sentiment."
    )
