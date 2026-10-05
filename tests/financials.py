"""Sample normalized financials shared by the fundamentals, valuation and API tests."""

from datetime import date, timedelta
from typing import Any

from app.models.fundamentals import AnnualValue, BalanceItem, Financials, FlowItem

PERIOD_END = date.today() - timedelta(days=60)
"""A recent quarter end, so the sample never looks stale."""
FISCAL_YEAR_END = PERIOD_END - timedelta(weeks=39)


def flow(concept: str, ttm: float, growth: float | None = 0.1) -> FlowItem:
    """A period item with a TTM total and one fiscal year."""
    return FlowItem(
        concept=concept,
        ttm=ttm,
        ttm_end=PERIOD_END,
        ttm_growth=growth,
        fiscal_years=[AnnualValue(fiscal_year_end=FISCAL_YEAR_END, value=ttm * 0.9)],
    )


def balance(concept: str, value: float, year_ago: float | None = None) -> BalanceItem:
    """A balance sheet item at the period end."""
    return BalanceItem(concept=concept, value=value, as_of=PERIOD_END, year_ago=year_ago)


def sample_financials(**overrides: Any) -> Financials:
    """Round-numbered financials of a profitable company, overriding any field."""
    fields: dict[str, Any] = {
        "cik": 320193,
        "entity_name": "Apple Inc.",
        "latest_period_end": PERIOD_END,
        "revenue": flow("Revenues", 400.0),
        "gross_profit": flow("GrossProfit", 200.0),
        "operating_income": flow("OperatingIncomeLoss", 120.0),
        "net_income": flow("NetIncomeLoss", 100.0),
        "operating_cash_flow": flow("NetCashProvidedByUsedInOperatingActivities", 110.0),
        "capital_expenditure": flow("PaymentsToAcquirePropertyPlantAndEquipment", 10.0),
        "depreciation_amortization": flow("DepreciationDepletionAndAmortization", 30.0),
        "free_cash_flow": flow("NetCashProvidedByUsedInOperatingActivities - Payments", 100.0),
        "ebitda": flow("OperatingIncomeLoss + DepreciationDepletionAndAmortization", 150.0),
        "cash": balance("CashAndCashEquivalentsAtCarryingValue", 50.0),
        "total_debt": balance("LongTermDebtNoncurrent + LongTermDebtCurrent", 100.0),
        "stockholders_equity": balance("StockholdersEquity", 300.0, year_ago=200.0),
        "shares_outstanding": balance("EntityCommonStockSharesOutstanding", 10.0),
    }
    return Financials(**(fields | overrides))
