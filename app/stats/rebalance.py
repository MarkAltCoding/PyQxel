"""Orders that move a portfolio to target weights.

Claude chooses the targets; the share counts are computed here, from the latest
prices, so the plan's arithmetic is exact.
"""

import math
from dataclasses import dataclass

CASH: str = "CASH"
"""The symbol a target uses for money left uninvested."""

MIN_TRADE_VALUE: float = 1.0
"""Trades worth less than this are left out as noise."""


@dataclass(frozen=True)
class Target:
    """A symbol's target weight and how to order it."""

    weight: float
    order_type: str
    limit_offset_percent: float | None
    rationale: str


@dataclass(frozen=True)
class Trade:
    """One order of the plan."""

    symbol: str
    action: str
    shares: float | None
    price: float | None
    value: float
    order_type: str
    limit_price: float | None
    rationale: str


@dataclass(frozen=True)
class Plan:
    """The orders, the value they were sized on, and what is left as cash."""

    total_value: float
    trades: list[Trade]
    cash_after: float
    notes: list[str]


def normalized(targets: dict[str, Target], holdings: dict[str, float]) -> dict[str, Target]:
    """``targets`` with any holding left out kept at its current weight, scaled down when
    the weights sum to more than one. Weight left over stays cash, so nothing is bought
    that the targets did not ask for.

    Args:
        targets: Target per symbol, as Claude gave them.
        holdings: Each current holding's weight of the total to invest.
    """
    complete = dict(targets)
    for symbol, weight in holdings.items():
        if symbol not in complete:
            complete[symbol] = Target(weight, "market", None, "Kept at its current weight.")
    total = math.fsum(target.weight for target in complete.values())
    if total <= 1:
        return complete
    return {
        symbol: Target(t.weight / total, t.order_type, t.limit_offset_percent, t.rationale)
        for symbol, t in complete.items()
    }


def plan_trades(
    holdings: dict[str, tuple[float, float]],
    targets: dict[str, Target],
    prices: dict[str, float | None],
    cash: float = 0.0,
) -> Plan:
    """Size the orders that take ``holdings`` and ``cash`` to ``targets``.

    Args:
        holdings: Each holding's ``(shares, price)``.
        targets: Target per symbol, including new ones and :data:`CASH`.
        prices: Latest prices of the new symbols; ``None`` where none was found.
        cash: New cash to invest.

    Returns:
        Sells first, then buys, in whole shares: buys round down so the plan never
        spends more than it has, and sells round to the nearest share, or sell the whole
        holding when the target is zero.
    """
    total = math.fsum(shares * price for shares, price in holdings.values()) + cash
    current = {symbol: shares * price / total for symbol, (shares, price) in holdings.items()}
    weights = normalized(targets, current) if total > 0 else targets
    notes: list[str] = []
    sells: list[Trade] = []
    buys: list[Trade] = []
    spent = 0.0
    for symbol, target in weights.items():
        if symbol == CASH:
            continue
        held_shares, held_price = holdings.get(symbol, (0.0, prices.get(symbol) or 0.0))
        price = held_price if symbol in holdings else prices.get(symbol)
        difference = target.weight * total - held_shares * (price or 0.0)
        if abs(difference) < MIN_TRADE_VALUE:
            continue
        if price is None or price <= 0:
            notes.append(f"{symbol} could not be priced: buy about {difference:,.2f} of it.")
            buys.append(
                Trade(
                    symbol, "buy", None, None, difference, target.order_type, None, target.rationale
                )
            )
            continue
        if difference < 0:
            shares = (
                held_shares if target.weight == 0 else min(held_shares, round(-difference / price))
            )
            action = "sell"
        else:
            shares = math.floor(difference / price)
            action = "buy"
        if shares <= 0:
            continue
        limit = None
        if target.order_type == "limit":
            offset = target.limit_offset_percent or 0.0
            limit = round(price * (1 + offset / 100), 2)
        trade = Trade(
            symbol,
            action,
            shares,
            price,
            shares * price,
            target.order_type,
            limit,
            target.rationale,
        )
        if action == "sell":
            sells.append(trade)
        else:
            buys.append(trade)
            spent += trade.value
    raised = math.fsum(trade.value for trade in sells)
    invested = math.fsum(shares * price for shares, price in holdings.values())
    cash_after = cash + raised - spent
    if invested + cash > 0 and cash_after < -MIN_TRADE_VALUE:
        notes.append("The buys cost more than the sells and new cash raise; trim them to fit.")
    return Plan(total_value=total, trades=sells + buys, cash_after=cash_after, notes=notes)
