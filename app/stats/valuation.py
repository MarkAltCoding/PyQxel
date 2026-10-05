"""Valuation multiples, margins and returns from a market value and financial statements."""

from app.models.fundamentals import Financials, FlowItem, MarketCapSource, Valuation


def _ttm(item: FlowItem | None) -> float | None:
    """The trailing-twelve-month figure of ``item``, if it has one."""
    return None if item is None else item.ttm


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    """``numerator / denominator``, or null unless both exist and the denominator is positive."""
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _market_cap(
    financials: Financials, price: float | None, market_cap: float | None
) -> tuple[float, MarketCapSource] | None:
    """The provider's market capitalization, or price times SEC-reported shares."""
    if market_cap is not None and market_cap > 0:
        return market_cap, "provider"
    shares = financials.shares_outstanding
    if price is not None and price > 0 and shares is not None and shares.value > 0:
        return price * shares.value, "shares_outstanding"
    return None


def value_company(
    financials: Financials, price: float | None, market_cap: float | None
) -> Valuation | None:
    """Combine the market value with ``financials`` into multiples, margins and returns.

    Args:
        financials: The company's normalized financial statements.
        price: Latest share price, in US dollars.
        market_cap: Market capitalization from the market data provider, which accounts
            for every share class; estimated from ``price`` and shares outstanding when
            missing.

    Returns:
        The valuation, or ``None`` when no market value can be established.
    """
    market = _market_cap(financials, price, market_cap)
    if market is None:
        return None
    value, source = market
    notes: list[str] = []
    if source == "shares_outstanding":
        notes.append(
            "Market cap is estimated as price times shares outstanding from SEC filings, "
            "which misstates it when share classes trade at different prices."
        )

    revenue = _ttm(financials.revenue)
    net_income = _ttm(financials.net_income)
    ebitda = _ttm(financials.ebitda)
    free_cash_flow = _ttm(financials.free_cash_flow)

    enterprise_value = None
    debt, cash = financials.total_debt, financials.cash
    if debt is not None and cash is not None:
        enterprise_value = value + debt.value - cash.value
    elif debt is None:
        notes.append(
            "Enterprise value is not computed: the company's debt is not reported in a "
            "recognized form."
        )
    else:
        notes.append("Enterprise value is not computed: cash is not reported.")

    if net_income is not None and net_income <= 0:
        notes.append("P/E is not meaningful: TTM net income is not positive.")
    if ebitda is None:
        notes.append(
            "EV/EBITDA is not computed: operating income or depreciation is not reported, "
            "as is usual for banks and insurers."
        )
    elif ebitda <= 0:
        notes.append("EV/EBITDA is not meaningful: TTM EBITDA is not positive.")
    if free_cash_flow is None:
        notes.append(
            "Free cash flow is not computed: operating cash flow or capital expenditure "
            "is not reported."
        )

    equity = financials.stockholders_equity
    average_equity = None
    if equity is not None:
        average_equity = (
            equity.value if equity.year_ago is None else (equity.value + equity.year_ago) / 2
        )
        if average_equity <= 0:
            notes.append("Return on equity is not meaningful: equity is not positive.")

    return Valuation(
        price=price,
        market_cap=value,
        market_cap_source=source,
        enterprise_value=enterprise_value,
        pe_ratio=_ratio(value, net_income),
        price_to_sales=_ratio(value, revenue),
        ev_to_ebitda=_ratio(enterprise_value, ebitda),
        fcf_yield=None if free_cash_flow is None else free_cash_flow / value,
        gross_margin=_ratio(_ttm(financials.gross_profit), revenue),
        operating_margin=_ratio(_ttm(financials.operating_income), revenue),
        net_margin=_ratio(net_income, revenue),
        fcf_margin=_ratio(free_cash_flow, revenue),
        return_on_equity=_ratio(net_income, average_equity),
        notes=notes,
    )
