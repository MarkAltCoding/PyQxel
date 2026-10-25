"""Schemas for a company's key metrics and peers from Finnhub's basic financials.

Ratios that Finnhub gives in percent are converted to decimals (0.25 = 25%), and figures
it gives in millions to units, as elsewhere in the API.
"""

from datetime import date

from pydantic import BaseModel, Field


class KeyMetrics(BaseModel):
    """Trading, valuation, dividend, profitability, growth and balance sheet metrics, as
    Finnhub computes them. Any may be null when Finnhub has no figure."""

    week_52_high: float | None = None
    week_52_high_date: date | None = None
    week_52_low: float | None = None
    week_52_low_date: date | None = None
    return_52_week: float | None = Field(default=None, description="Price return, decimal.")
    return_ytd: float | None = Field(default=None, description="Price return, decimal.")
    relative_to_sp500_52_week: float | None = Field(
        default=None, description="52-week price return less the S&P 500's, decimal."
    )
    beta: float | None = None
    average_volume_10_day: float | None = Field(default=None, description="Shares a day.")
    average_volume_3_month: float | None = Field(default=None, description="Shares a day.")

    market_cap: float | None = Field(default=None, description="In the listing's currency.")
    enterprise_value: float | None = None
    pe_ttm: float | None = None
    forward_pe: float | None = Field(
        default=None, description="Price over analysts' estimate of next year's EPS."
    )
    peg_ttm: float | None = None
    ps_ttm: float | None = None
    pb: float | None = None
    ev_ebitda_ttm: float | None = None
    ev_revenue_ttm: float | None = None
    price_to_fcf_ttm: float | None = None

    dividend_yield: float | None = Field(
        default=None, description="Indicated annual dividend over the price, decimal."
    )
    dividend_per_share_ttm: float | None = None
    payout_ratio_ttm: float | None = None
    dividend_growth_5y: float | None = Field(default=None, description="Annual rate, decimal.")

    gross_margin_ttm: float | None = None
    operating_margin_ttm: float | None = None
    net_margin_ttm: float | None = None
    roe_ttm: float | None = None
    roa_ttm: float | None = None
    roi_ttm: float | None = None

    revenue_growth_ttm: float | None = Field(
        default=None, description="TTM revenue over the TTM a year earlier, less one."
    )
    eps_growth_ttm: float | None = None
    revenue_growth_5y: float | None = Field(default=None, description="Annual rate, decimal.")
    eps_growth_5y: float | None = Field(default=None, description="Annual rate, decimal.")

    eps_ttm: float | None = None
    book_value_per_share: float | None = None
    current_ratio: float | None = None
    quick_ratio: float | None = None
    debt_to_equity: float | None = Field(default=None, description="Total debt over equity.")
    interest_coverage_ttm: float | None = None


class CompanyMetrics(BaseModel):
    """A company's key metrics and the companies Finnhub lists as its peers."""

    symbol: str
    metrics: KeyMetrics
    peers: list[str] = Field(
        description="Symbols of companies in the same industry, as Finnhub lists them; "
        "empty when it has none."
    )
