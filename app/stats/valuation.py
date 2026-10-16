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
    financials: Financials, price: float | None, market_cap: float | None, estimate: bool
) -> tuple[float, MarketCapSource] | None:
    """The provider's market capitalization, or, if ``estimate``, price times SEC shares."""
    if market_cap is not None and market_cap > 0:
        return market_cap, "provider"
    shares = financials.shares_outstanding
    if estimate and price is not None and price > 0 and shares is not None and shares.value > 0:
        return price * shares.value, "shares_outstanding"
    return None


def value_company(
    financials: Financials,
    price: float | None,
    market_cap: float | None,
    listing_currency: str = "USD",
    exchange_rate: float | None = None,
) -> Valuation | None:
    """Combine the market value with ``financials`` into multiples, margins and returns.

    Args:
        financials: The company's normalized financial statements.
        price: Latest share price, in ``listing_currency``.
        market_cap: Market capitalization from the market data provider, which accounts
            for every share class. When missing, it is estimated from ``price`` and
            shares outstanding, unless the statements are in another currency: a foreign
            issuer's shares are not its US-listed depositary shares.
        listing_currency: Currency of ``price`` and ``market_cap``.
        exchange_rate: Units of ``listing_currency`` per unit of the statements'
            currency, when the two differ. Without it, the multiples comparing statement
            figures with the market value are null; margins and returns are unaffected.

    Returns:
        The valuation, or ``None`` when no market value can be established.
    """
    same_currency = financials.currency.upper() == listing_currency.upper()
    market = _market_cap(financials, price, market_cap, estimate=same_currency)
    if market is None:
        return None
    value, source = market
    notes: list[str] = []
    if source == "shares_outstanding":
        notes.append(
            "Market cap is estimated as price times shares outstanding from SEC filings, "
            "which misstates it when share classes trade at different prices."
        )
    rate = 1.0 if same_currency else exchange_rate
    if rate is None:
        notes.append(
            f"P/E, P/S, EV/EBITDA and free-cash-flow yield are not computed: the financial "
            f"statements are in {financials.currency} and no exchange rate to "
            f"{listing_currency} is available."
        )
    elif not same_currency:
        notes.append(
            f"The financial statements are in {financials.currency}; they are compared with "
            f"the market value at the latest rate of {rate:.6g} {listing_currency} per "
            f"{financials.currency}, not the rates of the periods they cover."
        )

    def converted(amount: float | None) -> float | None:
        """``amount`` in the listing's currency, if it can be converted."""
        return None if amount is None or rate is None else amount * rate

    revenue = _ttm(financials.revenue)
    net_income = _ttm(financials.net_income)
    ebitda = _ttm(financials.ebitda)
    free_cash_flow = _ttm(financials.free_cash_flow)

    enterprise_value = None
    debt, cash = financials.total_debt, financials.cash
    if debt is not None and cash is not None:
        if rate is not None:
            enterprise_value = value + rate * (debt.value - cash.value)
    elif debt is None:
        notes.append(
            "Enterprise value is not computed: the company's debt is not reported in a "
            "recognized form."
        )
    else:
        notes.append("Enterprise value is not computed: cash is not reported.")

    if net_income is not None and net_income < 0:
        notes.append("P/E is negative: TTM net income is a loss.")
    elif net_income == 0:
        notes.append("P/E is not computed: TTM net income is zero.")
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
        pe_ratio=None if not (earnings := converted(net_income)) else value / earnings,
        price_to_sales=_ratio(value, converted(revenue)),
        ev_to_ebitda=_ratio(enterprise_value, converted(ebitda)),
        fcf_yield=None if (fcf := converted(free_cash_flow)) is None else fcf / value,
        gross_margin=_ratio(_ttm(financials.gross_profit), revenue),
        operating_margin=_ratio(_ttm(financials.operating_income), revenue),
        net_margin=_ratio(net_income, revenue),
        fcf_margin=_ratio(free_cash_flow, revenue),
        return_on_equity=_ratio(net_income, average_equity),
        exchange_rate=None if same_currency else rate,
        notes=notes,
    )
