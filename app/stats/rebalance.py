"""Orders that move a portfolio to target weights.

Claude chooses the targets; the share counts are computed here, from the latest
prices, so the plan's arithmetic is exact.
"""

import math
from dataclasses import dataclass

CASH: str = "Cash"
"""The key the planner files money left uninvested under: not a ticker, so it never
collides with a stock's (CASH itself is one)."""

MIN_TRADE_VALUE: float = 1.0
"""Trades worth less than this are left out as noise."""

SHARE_DECIMALS: int = 4
"""Decimals a fractional share count is given to, as brokers that trade fractions take."""

FRACTIONAL_NOTE: str = (
    "Fractional orders ({symbols}): not every broker trades fractions of every security, "
    "and many take them only as market orders. Stop and trailing-stop orders are not "
    "available for fractional shares at some brokers, including Fidelity. If yours does not "
    "take the fraction, sell it at market, or round the order to whole shares."
)

FRACTIONAL_LIMIT_NOTE: str = (
    "Fractional limit orders ({symbols}): some brokers, including Fidelity, accept them only "
    "as day orders (time in force: day), so an unfilled limit expires at the close and must "
    "be placed again; others do not accept them, and a market order is the way to trade "
    "the fraction."
)


@dataclass(frozen=True)
class Target:
    """A symbol's target weight and how to order it."""

    weight: float
    order_type: str
    limit_offset_percent: float | None
    rationale: str
    keep: bool = False
    """Leave the holding exactly as it is: no order, whatever the weight says."""


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
    fractional: bool = False
    """Whether ``shares`` is not a whole number, which not every broker or order type
    takes."""


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
            complete[symbol] = Target(
                weight, "market", None, "Kept at its current weight.", keep=True
            )
    total = math.fsum(target.weight for target in complete.values())
    if total <= 1:
        return complete
    return {
        symbol: Target(t.weight / total, t.order_type, t.limit_offset_percent, t.rationale, t.keep)
        for symbol, t in complete.items()
    }


def plan_trades(
    holdings: dict[str, tuple[float, float]],
    targets: dict[str, Target],
    prices: dict[str, float | None],
    cash: float = 0.0,
    fractional: bool = False,
) -> Plan:
    """Size the orders that take ``holdings`` and ``cash`` to ``targets``.

    Args:
        holdings: Each holding's ``(shares, price)``.
        targets: Target per symbol, including new ones and :data:`CASH`.
        prices: Latest prices of the new symbols; ``None`` where none was found.
        cash: New cash to invest.
        fractional: Whether the broker trades fractions of a share, so every order is
            sized to the fraction its target needs, to :data:`SHARE_DECIMALS` places.

    Returns:
        Sells first, then buys. Buys round down so the plan never spends more than it
        has, sells round to the nearest unit, and a target of zero sells the whole
        holding, fraction and all. With ``fractional``, the unit is a ten-thousandth of a
        share; without, a whole share, and a trade that whole shares would round away,
        such as one of under a share, is sized in fractional shares instead. Fractional
        orders are flagged, with notes on what brokers allow for fractions.
    """
    total = math.fsum(shares * price for shares, price in holdings.values()) + cash
    if total <= 0:
        return Plan(total_value=0.0, trades=[], cash_after=cash, notes=["Nothing to invest."])
    current = {symbol: shares * price / total for symbol, (shares, price) in holdings.items()}
    weights = normalized(targets, current) if total > 0 else targets
    notes: list[str] = []
    sells: list[Trade] = []
    buys: list[Trade] = []
    spent = 0.0
    for symbol, target in weights.items():
        if symbol == CASH or (target.keep and symbol in holdings):
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
        ideal = abs(difference) / price
        # The smallest amount an order can be: a whole share, or a ten-thousandth of one.
        unit = 10**SHARE_DECIMALS if fractional else 1
        if difference < 0:
            action = "sell"
            nearest = round(ideal * unit) / unit
            shares = held_shares if target.weight == 0 else min(held_shares, nearest)
        else:
            action = "buy"
            shares = math.floor(ideal * unit) / unit
        if shares <= 0:
            # Whole shares round this trade away; a fraction of a share carries it out.
            factor = 10**SHARE_DECIMALS
            shares = math.floor(min(ideal, held_shares if action == "sell" else ideal) * factor)
            shares /= factor
            if shares * price < MIN_TRADE_VALUE:
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
            fractional=not float(shares).is_integer(),
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
    trades = sells + buys
    fractions = [trade.symbol for trade in trades if trade.fractional]
    if fractions:
        notes.append(FRACTIONAL_NOTE.format(symbols=", ".join(fractions)))
    limits = [t.symbol for t in trades if t.fractional and t.order_type == "limit"]
    if limits:
        notes.append(FRACTIONAL_LIMIT_NOTE.format(symbols=", ".join(limits)))
    return Plan(total_value=total, trades=trades, cash_after=cash_after, notes=notes)
