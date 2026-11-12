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

TIDY_STEPS: tuple[float, ...] = (1.0, 0.5, 0.25, 0.1, 0.01, 10**-SHARE_DECIMALS)
"""Share counts an order may be rounded to when tidy ones are preferred, coarsest first:
whole shares, halves, quarters, tenths, hundredths, and the exact fraction."""

TIDY_SLACK: float = 0.0025
"""How far, as a share of the portfolio's value, a tidy order may leave a position from
its target: a quarter of a percent, $25 of a $10,000 portfolio."""

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


def tidy_shares(ideal: float, price: float, slack: float, most: float) -> float:
    """The tidiest share count near ``ideal``: the first of :data:`TIDY_STEPS` whose
    nearest multiple, or the one below it, is worth within ``slack`` of ``ideal`` and no
    more than ``most`` shares. So 3.438 becomes 3.5, and 1.002 becomes 1, when that is
    close enough; otherwise the exact fraction, rounded down. It may be zero."""
    for step in TIDY_STEPS:
        for multiple in (round(ideal / step), math.floor(ideal / step)):
            shares = round(float(multiple) * step, SHARE_DECIMALS)
            if 0 <= shares <= most + 1e-9 and abs(shares - ideal) * price <= slack:
                return shares
    return float(math.floor(min(ideal, most) * 10**SHARE_DECIMALS) / 10**SHARE_DECIMALS)


def plan_trades(
    holdings: dict[str, tuple[float, float]],
    targets: dict[str, Target],
    prices: dict[str, float | None],
    cash: float = 0.0,
    fractional: bool = False,
    tidy: bool = False,
) -> Plan:
    """Size the orders that take ``holdings`` and ``cash`` to ``targets``.

    Args:
        holdings: Each holding's ``(shares, price)``.
        targets: Target per symbol, including new ones and :data:`CASH`.
        prices: Latest prices of the new symbols; ``None`` where none was found.
        cash: New cash to invest.
        fractional: Whether the broker trades fractions of a share, so every order is
            sized to the fraction its target needs, to :data:`SHARE_DECIMALS` places.
        tidy: With ``fractional``, leave each holding at a whole number of shares or a
            round fraction such as 3.5 where that is within :data:`TIDY_SLACK` of the
            portfolio's value of its target (:func:`tidy_shares`), and send no order for
            one already that close. A buy is rounded up only as far as the cash left after
            the sells and the buys before it allows.

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
    spent = raised = 0.0

    def gap(item: tuple[str, Target]) -> float:
        """How far the symbol's value is below its target: negative for a sale."""
        symbol, target = item
        held_shares, held_price = holdings.get(symbol, (0.0, prices.get(symbol) or 0.0))
        price = held_price if symbol in holdings else prices.get(symbol)
        return target.weight * total - held_shares * (price or 0.0)

    # Sales first, so a tidy buy knows the cash they raise.
    for symbol, target in sorted(weights.items(), key=lambda item: gap(item) >= 0):
        if symbol == CASH or (target.keep and symbol in holdings):
            continue
        held_shares, held_price = holdings.get(symbol, (0.0, prices.get(symbol) or 0.0))
        price = held_price if symbol in holdings else prices.get(symbol)
        difference = gap((symbol, target))
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
        slack = TIDY_SLACK * total
        if difference < 0:
            action = "sell"
            nearest = round(ideal * unit) / unit
            if target.weight == 0:
                shares = held_shares
            elif fractional and tidy:
                # The holding left is what is tidied: 10 shares less 2.5 leaves 7.5.
                left = tidy_shares(held_shares - ideal, price, slack, held_shares)
                shares = round(held_shares - left, SHARE_DECIMALS)
                if shares <= 0:
                    continue  # Already within reach of its target.
            else:
                shares = min(held_shares, nearest)
        else:
            action = "buy"
            if fractional and tidy:
                # The holding after the buy is tidied, rounding up only as far as the cash
                # left goes; rounding down is always allowed.
                affordable = max(cash + raised - spent, 0.0) / price
                after = tidy_shares(
                    held_shares + ideal, price, slack, held_shares + max(affordable, ideal)
                )
                shares = round(after - held_shares, SHARE_DECIMALS)
                if shares <= 0:
                    continue
            else:
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
            raised += trade.value
        else:
            buys.append(trade)
            spent += trade.value
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
