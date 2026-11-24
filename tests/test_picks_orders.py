"""Tests for the orders behind Claude's Picks: how they are settled when the picks are made,
and whether, when, and at what price daily bars say each would have filled."""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from app.models.picks import PickOrder
from app.stats.picks_orders import replay, sessions_after, settle_order
from app.stats.picks_performance import track
from tests.test_picks_performance import CLOSES, _picks

DAYS = pd.to_datetime(["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"])


def _bars(
    opens: list[float], highs: list[float], lows: list[float], closes: list[float]
) -> pd.DataFrame:
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes}, index=DAYS[: len(opens)]
    )


def _limit(price: float, **changes: object) -> PickOrder:
    fields: dict[str, object] = {
        "order_type": "limit",
        "limit_price": price,
        "time_in_force": "gtc",
        "good_for_days": 3,
        "if_unfilled": "skip",
        "cancel_after": date(2026, 11, 18),
    }
    fields.update(changes)
    return PickOrder.model_validate(fields)


def test_a_limit_fills_only_when_the_low_reaches_it_at_the_limit() -> None:
    bars = _bars([100, 99, 98, 97], [101, 100, 99, 99], [99, 98.5, 96, 95], [100, 99, 97, 98])

    played = replay(_limit(97.0), bars, date(2026, 11, 16), date(2026, 11, 19))

    assert played is not None and played.status == "held"
    assert played.filled_on == date(2026, 11, 18) and played.fill_price == 97.0
    assert list(played.value) == pytest.approx([1.0, 1.0, 1.0, 98 / 97])


def test_a_gap_below_the_limit_fills_at_the_open() -> None:
    bars = _bars([100, 95], [101, 96], [99, 94], [100, 95])

    played = replay(_limit(97.0), bars, date(2026, 11, 16), date(2026, 11, 17))

    assert played is not None and played.fill_price == 95.0


def test_an_order_that_never_fills_leaves_the_money_in_cash() -> None:
    bars = _bars([100, 101, 102, 103], [101, 102, 103, 104], [99.5, 100, 101, 102], [100] * 4)

    played = replay(_limit(97.0), bars, date(2026, 11, 16), date(2026, 11, 19))

    assert played is not None and played.status == "not_filled" and played.filled_on is None
    assert set(played.value) == {1.0}
    assert "stayed in cash" in (played.note or "")


def test_an_order_still_open_is_waiting() -> None:
    bars = _bars([100, 101], [101, 102], [99.5, 100], [100, 101])

    played = replay(_limit(97.0), bars, date(2026, 11, 16), date(2026, 11, 17))

    assert played is not None and played.status == "waiting"


def test_the_contingency_buys_at_the_next_open_after_the_order_is_canceled() -> None:
    bars = _bars([100, 101, 102, 103], [101, 102, 103, 105], [99.5, 100, 101, 102], [100] * 4)

    played = replay(
        _limit(97.0, if_unfilled="buy_at_market"), bars, date(2026, 11, 16), date(2026, 11, 19)
    )

    assert played is not None and played.status == "held"
    assert played.filled_on == date(2026, 11, 19) and played.fill_price == 103.0


def test_a_stop_loss_sells_from_the_next_session_and_a_gap_down_sells_at_the_open() -> None:
    order = _limit(100.0, stop_loss=95.0)
    bars = _bars([100, 99, 90, 92], [101, 100, 91, 95], [99, 96, 89, 91], [100, 98, 90, 93])

    played = replay(order, bars, date(2026, 11, 16), date(2026, 11, 19))

    assert played is not None and played.status == "sold" and played.sold_by == "stop_loss"
    assert played.sold_on == date(2026, 11, 18) and played.exit_price == 90.0
    assert list(played.value)[-2:] == pytest.approx([0.9, 0.9])


def test_a_session_reaching_both_exits_sells_at_the_stop_loss() -> None:
    order = _limit(100.0, stop_loss=95.0, take_profit=110.0)
    bars = _bars([100, 100], [101, 111], [99, 94], [100, 100])

    played = replay(order, bars, date(2026, 11, 16), date(2026, 11, 17))

    assert played is not None and played.sold_by == "stop_loss" and played.exit_price == 95.0


def test_a_stop_limit_buys_once_the_price_reaches_the_stop() -> None:
    order = _limit(106.0, order_type="stop_limit", stop_price=105.0)
    bars = _bars([100, 103, 104], [102, 104, 107], [99, 102, 103], [101, 103, 106])

    played = replay(order, bars, date(2026, 11, 16), date(2026, 11, 18))

    assert played is not None and played.filled_on == date(2026, 11, 18)
    assert played.fill_price == 105.0


def test_settling_turns_slips_into_something_a_broker_takes() -> None:
    first, last = date(2026, 11, 16), date(2026, 12, 31)

    far = settle_order(_limit(20.0), 100.0, first, last)
    assert far.order_type == "market" and far.limit_price is None and far.if_unfilled == "skip"

    wrong_exits = settle_order(_limit(98.0, stop_loss=99.0, take_profit=97.0), 100.0, first, last)
    assert wrong_exits.stop_loss is None and wrong_exits.take_profit is None

    gtc = settle_order(_limit(98.0, good_for_days=5), 100.0, first, last)
    assert gtc.cancel_after == sessions_after(first, 5) == date(2026, 11, 20)

    day = settle_order(_limit(98.0, time_in_force="day"), 100.0, first, last)
    assert day.cancel_after == first and day.good_for_days is None

    assert settle_order(None, 100.0, first, last).order_type == "market"


def test_a_pick_whose_order_never_filled_counts_as_cash_in_the_lists_return() -> None:
    picks = _picks(datetime(2026, 11, 16, 23, tzinfo=timezone.utc))
    report = picks.report.model_copy(
        update={
            "picks": [
                (
                    pick.model_copy(update={"order": _limit(50.0, cancel_after=date(2026, 11, 18))})
                    if pick.symbol == "BIG"
                    else pick
                )
                for pick in picks.report.picks
            ]
        }
    )
    picks = picks.model_copy(update={"report": report})
    big = CLOSES["BIG"]
    bars = {"BIG": pd.DataFrame({"Open": big, "High": big, "Low": big, "Close": big})}

    result = track(picks, CLOSES, "SPY", date(2026, 11, 19), bars)

    found = {p.symbol: p for p in result.picks}
    assert found["BIG"].status == "not_filled" and found["BIG"].gain == 0
    assert "BIG was not bought" in (result.notice or "")
