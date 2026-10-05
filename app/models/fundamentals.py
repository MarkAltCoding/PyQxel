"""Schemas for financial statement data from SEC filings and the valuation built on it.

Money amounts are US dollars and share counts are shares. Growth rates, margins, yields
and returns are decimals (0.25 = 25%); valuation multiples are plain ratios.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

FlowItemName = Literal[
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "capital_expenditure",
    "depreciation_amortization",
]
"""Income and cash flow statement items read from filings, each covering a period."""

BalanceItemName = Literal["cash", "stockholders_equity"]
"""Balance sheet items read from single concepts, each measured at a date."""

MarketCapSource = Literal["provider", "shares_outstanding"]
"""``provider`` when the market data provider reported the market capitalization,
``shares_outstanding`` when it was estimated as price times SEC-reported shares."""


class AnnualValue(BaseModel):
    """One fiscal year's figure, identified by the date the fiscal year ended."""

    fiscal_year_end: date
    value: float
    growth: float | None = Field(
        default=None,
        description="Change from the previous fiscal year; null without that year or when "
        "it was zero or negative.",
    )


class FlowItem(BaseModel):
    """An income or cash flow statement item over the trailing twelve months and by year."""

    concept: str = Field(description="XBRL concept the figures come from, e.g. ``Revenues``.")
    ttm: float | None = Field(
        default=None, description="Trailing-twelve-month total ending at ``ttm_end``."
    )
    ttm_end: date | None = Field(default=None, description="Last day the TTM figure covers.")
    ttm_growth: float | None = Field(
        default=None,
        description="Change from the TTM figure a year earlier; null without it or when it "
        "was zero or negative.",
    )
    fiscal_years: list[AnnualValue] = Field(
        default_factory=list, description="Recent fiscal years, oldest first."
    )


class BalanceItem(BaseModel):
    """A balance sheet item at the latest balance sheet date."""

    concept: str = Field(description="XBRL concept, or concepts summed, the value comes from.")
    value: float
    as_of: date
    year_ago: float | None = Field(
        default=None, description="The same item about a year before ``as_of``, if reported."
    )


class Financials(BaseModel):
    """A company's financial statement figures, normalized from its SEC XBRL filings.

    Periods come from each figure's own start and end dates, so fiscal years that do not
    match the calendar line up correctly. Figures are as filed under US GAAP, using the
    latest filing's value when a period was restated.
    """

    cik: int = Field(description="The company's SEC Central Index Key.")
    entity_name: str
    latest_period_end: date | None = Field(
        default=None, description="End of the most recent period any statement covers."
    )
    revenue: FlowItem | None = None
    gross_profit: FlowItem | None = None
    operating_income: FlowItem | None = None
    net_income: FlowItem | None = None
    operating_cash_flow: FlowItem | None = None
    capital_expenditure: FlowItem | None = Field(
        default=None, description="Purchases of property, plant and equipment, as a positive sum."
    )
    depreciation_amortization: FlowItem | None = None
    free_cash_flow: FlowItem | None = Field(
        default=None, description="Operating cash flow less capital expenditure."
    )
    ebitda: FlowItem | None = Field(
        default=None, description="Operating income plus depreciation and amortization."
    )
    cash: BalanceItem | None = Field(default=None, description="Cash and cash equivalents.")
    total_debt: BalanceItem | None = Field(
        default=None,
        description="Short- and long-term borrowings, excluding operating leases; null when "
        "the company does not report its long-term debt in a recognized form.",
    )
    stockholders_equity: BalanceItem | None = Field(
        default=None, description="Equity attributable to the company's shareholders."
    )
    shares_outstanding: BalanceItem | None = None


class Valuation(BaseModel):
    """Valuation multiples and returns from the current market value and the financials.

    Multiples combine today's market value with trailing figures that may be a few months
    old. A ratio is null when an input is missing or the ratio is not meaningful, such as
    a P/E with negative earnings; ``notes`` say why.
    """

    price: float | None = Field(default=None, ge=0)
    market_cap: float = Field(ge=0)
    market_cap_source: MarketCapSource
    enterprise_value: float | None = Field(
        default=None, description="Market capitalization plus total debt less cash."
    )
    pe_ratio: float | None = Field(default=None, description="Market cap over TTM net income.")
    price_to_sales: float | None = Field(default=None, description="Market cap over TTM revenue.")
    ev_to_ebitda: float | None = Field(
        default=None, description="Enterprise value over TTM EBITDA."
    )
    fcf_yield: float | None = Field(
        default=None, description="TTM free cash flow over market cap; negative when FCF is."
    )
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    fcf_margin: float | None = None
    return_on_equity: float | None = Field(
        default=None,
        description="TTM net income over average equity of the latest and year-earlier "
        "balance sheets, or the latest alone.",
    )
    notes: list[str] = Field(
        default_factory=list, description="Why ratios are missing or approximate."
    )


class Fundamentals(BaseModel):
    """Financial statement figures and the valuation built on them."""

    financials: Financials
    valuation: Valuation | None = Field(
        default=None, description="Null when no market value is available."
    )


class FundamentalsResponse(Fundamentals):
    """A security's fundamentals as returned by the API."""

    symbol: str
    notice: str | None = Field(default=None, description="Explains missing or stale data, if any.")
