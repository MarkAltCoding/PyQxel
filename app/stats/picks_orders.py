"""The orders behind Claude's Picks: checked when the picks are made, and replayed on daily
bars to say whether, when, and at what price each would have filled.

Daily bars hold a session's open, high, low, and close, not the order prices came in, so
the replay takes the conservative reading where a bar allows two:

- A buy limit fills on the first session whose low reaches the limit, at the limit, or at
  the open when the session opens below it (a gap down fills at the better price).
- A buy stop-limit is set off on the first session whose high reaches the stop; it then
  fills at the stop, or at the open when the session opens above the stop and still at or
  under the limit. Set off but above the limit, it waits as a limit order.
- A market order fills at the open of the session the holding begins, or, when the list
  was made during a session, at that session's close, the nearest price daily bars give.
- An order not filled by its last session is canceled. Its contingency then either keeps
  the money in cash or buys at the next session's open.
- Once bought, the stop-loss and profit target are watched from the next session on, since
  a bar cannot say whether the fill came before the day's low or after it. A session that
  reaches both sells at the stop-loss, unless it opened above the target. A gap through
  either fills at the open.

Prices are the provider's adjusted ones. Claude set the order's prices against the price
on the day the list was made, and later dividends lower the adjusted prices before them,
so the order's prices are scaled by the same factor before they are compared.
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import pandas as pd

from app.models.picks import Pick, PickOrder
from app.stats.schedule import is_trading_day

FAR_FROM_PRICE: float = 0.5
"""How far from the latest price, as a share of it, an order's price may be before it is
taken as a slip and the order made a market order: a limit at half or 1.5 times the
price."""


def sessions_after(day: date, count: int) -> date:
    """The ``count``-th NYSE session from ``day``, counting ``day`` itself as the first
    when it is one."""
    current = day
    while not is_trading_day(current):
        current += timedelta(days=1)
    seen = 1
    while seen < count:
        current += timedelta(days=1)
        if is_trading_day(current):
            seen += 1
    return current


def _sane(value: float | None, price: float | None) -> float | None:
    """``value`` rounded to the cent when it is a positive price near ``price``; else
    ``None``."""
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    if price is not None and not (
        price * (1 - FAR_FROM_PRICE) <= value <= price * (1 + FAR_FROM_PRICE)
    ):
        return None
    return round(value, 2) if value >= 1 else round(value, 4)


def settle_order(
    order: PickOrder | None, price: float | None, first_session: date, last_session: date
) -> PickOrder:
    """``order`` made consistent and dated: a limit without a usable price becomes a
    market order, a stop-limit without a stop a limit, a stop-loss above the purchase or a
    target below it is dropped, a good-'til-canceled order is kept open no longer than the
    holding period, and ``cancel_after`` names its last session. A pick given no order
    gets a market order, the simplest to follow. ``price`` is the latest price Claude saw.
    """
    if order is None:
        order = PickOrder(order_type="market", time_in_force="day", if_unfilled="skip")
    limit = _sane(order.limit_price, price)
    stop = _sane(order.stop_price, price)
    kind = order.order_type
    if kind == "stop_limit" and stop is None:
        kind = "limit"
    if kind in ("limit", "stop_limit") and limit is None:
        kind, stop = "market", None
    if kind == "stop_limit" and stop is not None and limit is not None and limit < stop:
        limit = stop
    if kind != "stop_limit":
        stop = None
    if kind == "market":
        limit = None
    # What the shares are expected to cost, for checking the exits against.
    paid = limit if limit is not None else price
    stop_loss = _sane(order.stop_loss, price)
    take_profit = _sane(order.take_profit, price)
    if stop_loss is not None and paid is not None and stop_loss >= paid:
        stop_loss = None
    if take_profit is not None and paid is not None and take_profit <= paid:
        take_profit = None
    good_for = None
    tif: Literal["day", "gtc"]
    if kind == "market" or order.time_in_force == "day":
        tif = "day"
        cancel_after = first_session
    else:
        tif = "gtc"
        good_for = max(1, order.good_for_days or 10)
        cancel_after = min(sessions_after(first_session, good_for), last_session)
    return PickOrder(
        order_type=kind,
        limit_price=limit,
        stop_price=stop,
        time_in_force=tif,
        good_for_days=good_for,
        if_unfilled="skip" if kind == "market" else order.if_unfilled,
        stop_loss=stop_loss,
        take_profit=take_profit,
        cancel_after=cancel_after,
    )


def settle_orders(
    picks: list[Pick], prices: dict[str, float], first_session: date, last_session: date
) -> list[Pick]:
    """Every pick with its order settled by :func:`settle_order`."""
    return [
        pick.model_copy(
            update={
                "order": settle_order(
                    pick.order, prices.get(pick.symbol), first_session, last_session
                )
            }
        )
        for pick in picks
    ]


@dataclass(frozen=True)
class Replay:
    """What one pick's orders would have done over its holding period."""

    status: str
    """``held``, ``waiting``, ``not_filled``, or ``sold``, as in
    :class:`~app.models.picks.PickPerformance`."""
    value: pd.Series
    """The money given to the pick on each session, as a multiple of what it started as:
    1 while it waits in cash."""
    filled_on: date | None = None
    fill_price: float | None = None
    sold_on: date | None = None
    sold_by: str | None = None
    exit_price: float | None = None
    note: str | None = None


def _money(value: float) -> str:
    return f"${value:,.2f}"


def replay(
    order: PickOrder,
    bars: pd.DataFrame,
    first_session: date,
    finish: date,
    scale: float = 1.0,
    during_session: bool = False,
) -> Replay | None:
    """Replay ``order`` on ``bars`` (``Open``, ``High``, ``Low``, ``Close``, indexed by
    date) from ``first_session`` to ``finish``, with its prices times ``scale``. ``None``
    when there are no bars in that time."""
    window = bars[
        (bars.index >= pd.Timestamp(first_session)) & (bars.index <= pd.Timestamp(finish))
    ].dropna(subset=["Close"])
    if window.empty:
        return None
    days = [pd.Timestamp(day).date() for day in window.index]
    opens = window["Open"].fillna(window["Close"]).to_numpy(dtype=float)
    highs = window["High"].fillna(window["Close"]).to_numpy(dtype=float)
    lows = window["Low"].fillna(window["Close"]).to_numpy(dtype=float)
    closes = window["Close"].to_numpy(dtype=float)
    limit = None if order.limit_price is None else order.limit_price * scale
    stop = None if order.stop_price is None else order.stop_price * scale
    cancel_after = order.cancel_after or first_session

    fill_at: int | None = None
    price: float | None = None
    triggered = False
    for i, day in enumerate(days):
        if day > cancel_after:
            break
        if order.order_type == "market":
            fill_at, price = i, closes[i] if during_session else opens[i]
            break
        if order.order_type == "stop_limit" and not triggered and stop is not None:
            if highs[i] < stop:
                continue
            triggered = True
            start = max(opens[i], stop)
            if limit is not None and start <= limit:
                fill_at, price = i, start
                break
        if limit is not None and lows[i] <= limit:
            fill_at, price = i, min(opens[i], limit)
            break

    note: str | None = None
    if fill_at is None:
        after = next((i for i, day in enumerate(days) if day > cancel_after), None)
        if after is None:
            waiting = days[-1] <= cancel_after
            status = "waiting" if waiting else "not_filled"
            note = (
                f"The buy order is open until the close on {cancel_after:%b %-d}, unfilled so far."
                if waiting
                else "The buy order never filled, so the money stayed in cash."
            )
            return Replay(status=status, value=pd.Series(1.0, index=window.index), note=note)
        if order.if_unfilled == "skip":
            return Replay(
                status="not_filled",
                value=pd.Series(1.0, index=window.index),
                note=f"The buy order did not fill by {cancel_after:%b %-d}; as planned, the "
                "money stayed in cash.",
            )
        fill_at, price = after, opens[after]
        note = (
            f"The buy order did not fill by {cancel_after:%b %-d}, so, as planned, it was "
            f"bought at the next open, {_money(price / scale)}."
        )
    assert price is not None and price > 0
    if note is None:
        note = f"Bought {days[fill_at]:%b %-d} at {_money(price / scale)}."

    values = [1.0] * fill_at
    stop_loss = None if order.stop_loss is None else order.stop_loss * scale
    target = None if order.take_profit is None else order.take_profit * scale
    sold_at: int | None = None
    exit_price: float | None = None
    sold_by: str | None = None
    values.append(closes[fill_at] / price)
    for i in range(fill_at + 1, len(days)):
        if target is not None and opens[i] >= target:
            sold_at, exit_price, sold_by = i, opens[i], "take_profit"
        elif stop_loss is not None and lows[i] <= stop_loss:
            sold_at, exit_price, sold_by = i, min(opens[i], stop_loss), "stop_loss"
        elif target is not None and highs[i] >= target:
            sold_at, exit_price, sold_by = i, target, "take_profit"
        if sold_at is not None:
            break
        values.append(closes[i] / price)
    if sold_at is not None and exit_price is not None:
        values.extend([exit_price / price] * (len(days) - sold_at))
        name = "stop-loss" if sold_by == "stop_loss" else "profit target"
        note += f" Its {name} sold it {days[sold_at]:%b %-d} at {_money(exit_price / scale)}."
    return Replay(
        status="sold" if sold_at is not None else "held",
        value=pd.Series(values, index=window.index),
        filled_on=days[fill_at],
        fill_price=price,
        sold_on=None if sold_at is None else days[sold_at],
        sold_by=sold_by,
        exit_price=exit_price,
        note=note,
    )
