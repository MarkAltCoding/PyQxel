"""Schemas for AI-written investment theses and risk summaries, and the SEC filings behind them.

:class:`InvestmentThesis` and :class:`RiskSummary` double as the structured-output
schemas Claude fills in, so they use only plain types and literals.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.stock import HistoryCoverage, TickerInfo
from app.models.volatility import VolatilityPeriod

AnalysisKind = Literal["thesis", "risk"]
"""Report types: an investment thesis, or a risk summary."""

AnalysisPeriod = VolatilityPeriod
"""Lookback windows an analysis may cover; ``6mo`` or more gives a usable sample."""


FilingForm = Literal["10-K", "10-Q"]
"""SEC forms read for an analysis: the annual and the quarterly report."""


class AnalysisRequest(BaseModel):
    """Options for an AI-written analysis."""

    kind: AnalysisKind = Field(
        default="thesis",
        description="``thesis`` for an investment thesis, ``risk`` for a risk summary.",
    )
    period: AnalysisPeriod = Field(default="1y", description="Lookback window of daily bars.")
    include_filings: bool = Field(
        default=True,
        description="Also give Claude the Risk Factors and MD&A sections of the latest "
        "10-K and any 10-Q filed since, from SEC EDGAR.",
    )


class FilingSection(BaseModel):
    """The plain text of one item of a filing, such as Risk Factors."""

    title: str
    text: str
    truncated: bool = Field(description="Whether the text was cut at the configured length.")


class FilingMetadata(BaseModel):
    """Identifies one SEC filing."""

    form: FilingForm
    accession_number: str
    filed: date = Field(description="Date the filing was accepted by the SEC.")
    period_of_report: date | None = Field(
        default=None, description="Fiscal period end the filing covers."
    )
    url: str = Field(description="The filing's primary document on sec.gov.")


class FilingReference(FilingMetadata):
    """A filing an analysis read, without its text."""

    sections: list[str] = Field(description="Titles of the sections given to the model.")
    truncated_sections: list[str] = Field(
        default_factory=list, description="Sections cut at the configured length."
    )


class Filing(FilingMetadata):
    """A filing and the text of the sections extracted from it."""

    sections: list[FilingSection]

    def reference(self) -> FilingReference:
        """Return this filing's metadata and section titles, without the text."""
        return FilingReference(
            **self.model_dump(exclude={"sections"}),
            sections=[section.title for section in self.sections],
            truncated_sections=[s.title for s in self.sections if s.truncated],
        )


class PriceSummary(BaseModel):
    """Return, volatility and drawdown statistics of daily closes over a window.

    Returns and volatilities are decimals (0.25 = 25%); volatilities are annualized.
    """

    start: datetime = Field(description="First bar in the window.")
    end: datetime = Field(description="Last bar in the window.")
    observations: int = Field(ge=1, description="Number of returns.")
    first_close: float = Field(gt=0)
    last_close: float = Field(gt=0)
    high: float = Field(gt=0, description="Highest close in the window.")
    low: float = Field(gt=0, description="Lowest close in the window.")
    period_return: float = Field(description="Total price return over the window.")
    annualized_return: float | None = Field(
        default=None, description="Compound annual return; null for windows under a year."
    )
    realized_volatility: float = Field(ge=0, description="Sample volatility of daily returns.")
    ewma_volatility: float | None = Field(
        default=None,
        ge=0,
        description="RiskMetrics EWMA volatility for the next bar, weighting recent returns.",
    )
    max_drawdown: float = Field(le=0, description="Largest peak-to-trough decline.")
    current_drawdown: float = Field(le=0, description="Decline of the last close from its peak.")
    best_return: float = Field(description="Largest single-bar return.")
    worst_return: float = Field(description="Largest single-bar loss.")


class AnalysisContext(BaseModel):
    """The market data an analysis is written from, sent to Claude verbatim.

    Filing text is sent alongside it; the context lists only which filings were read.
    """

    ticker: TickerInfo
    period: AnalysisPeriod
    prices: PriceSummary
    coverage: HistoryCoverage
    filings: list[FilingReference] = Field(
        default_factory=list, description="SEC filings whose sections were given to the model."
    )
    notice: str | None = Field(
        default=None, description="Explains which part of the window has no data, if any."
    )


class InvestmentThesis(BaseModel):
    """A data-grounded investment thesis."""

    headline: str = Field(description="One-sentence thesis.")
    stance: Literal["bullish", "neutral", "bearish"]
    conviction: Literal["low", "medium", "high"]
    summary: str = Field(description="Two or three paragraphs developing the thesis.")
    supporting_points: list[str] = Field(description="Evidence for the stance, citing figures.")
    counterpoints: list[str] = Field(description="Evidence against the stance.")
    what_would_change_the_view: list[str] = Field(
        description="Observable developments that would invalidate the thesis."
    )
    data_limitations: list[str] = Field(description="What the provided data cannot show.")


class RiskFactor(BaseModel):
    """One risk and the evidence for it."""

    name: str
    severity: Literal["low", "moderate", "elevated", "high"]
    evidence: str = Field(description="Figures from the data that support this risk.")


class RiskSummary(BaseModel):
    """A data-grounded risk summary."""

    headline: str = Field(description="One-sentence risk assessment.")
    risk_level: Literal["low", "moderate", "elevated", "high"]
    summary: str = Field(description="Two or three paragraphs describing the risk profile.")
    volatility_assessment: str
    drawdown_assessment: str
    key_risks: list[RiskFactor]
    data_limitations: list[str] = Field(description="What the provided data cannot show.")


class AnalysisResponse(BaseModel):
    """An AI-written report together with the data it was written from."""

    symbol: str
    kind: AnalysisKind
    report: InvestmentThesis | RiskSummary
    context: AnalysisContext
    model: str = Field(description="Claude model that wrote the report.")
    generated_at: datetime
    disclaimer: str = (
        "Generated by an AI model from historical price data and, where listed, excerpts "
        "of SEC filings. Not investment advice."
    )
