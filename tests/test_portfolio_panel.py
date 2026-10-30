"""Tests for multi-asset requests, concurrent price downloads, and aligned return panels.

Downloads are replaced with fakes, so no test touches the network.
"""

import asyncio

import numpy as np
import pandas as pd
import pytest
from pydantic import TypeAdapter, ValidationError

from app.data import panel as panel_data
from app.data.fetcher import DataFetchError, SymbolNotFoundError
from app.data.panel import fetch_close_panel
from app.models.portfolio import MAX_SYMBOLS, Portfolio, SymbolList
from app.stats.copulas import pairwise_kendall
from app.stats.panel import return_panel, usable_return_panel
from app.stats.volatility import InsufficientDataError

symbol_list = TypeAdapter(SymbolList)


def _bars(
    closes: list[float], start: str = "2026-01-05", tz: str = "America/New_York"
) -> pd.DataFrame:
    """Business-day OHLCV bars at midnight exchange time, as yfinance returns them."""
    index = pd.bdate_range(start, periods=len(closes)).tz_localize(tz)
    frame = pd.DataFrame(
        {column: closes for column in ["Open", "High", "Low", "Close"]}, index=index
    )
    frame["Volume"] = 1_000
    return frame


def _walk(days: int, seed: int) -> list[float]:
    """A random price path of ``days`` closes."""
    rng = np.random.default_rng(seed)
    return list(100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, size=days)))


def _dated_closes(columns: dict[str, list[float]], start: str = "2025-01-02") -> pd.DataFrame:
    """Closes on business days from ``start``, one column per symbol."""
    length = max(len(values) for values in columns.values())
    index = pd.bdate_range(start, periods=length)
    return pd.DataFrame(
        {symbol: [*([np.nan] * (length - len(v))), *v] for symbol, v in columns.items()},
        index=index,
    )


# --- Request models ---


def test_symbol_list_is_normalized() -> None:
    """Symbols are stripped and upper-cased."""
    assert symbol_list.validate_python([" aapl", "brk-b", "^gspc"]) == ["AAPL", "BRK-B", "^GSPC"]


@pytest.mark.parametrize(
    ("symbols", "message"),
    [
        (["AAPL"], "at least 2"),
        ([f"S{i}" for i in range(MAX_SYMBOLS + 1)], f"at most {MAX_SYMBOLS}"),
        (["AAPL", "msft", "aapl ", "MSFT"], "repeated: AAPL, MSFT"),
        (["AAPL", "BAD$"], "pattern"),
    ],
)
def test_symbol_list_rejects_bad_sets(symbols: list[str], message: str) -> None:
    """Too few or too many symbols, repeats after normalization, and bad symbols fail."""
    with pytest.raises(ValidationError, match=message.replace("$", r"\$")):
        symbol_list.validate_python(symbols)


def test_portfolio_exposes_symbols_and_weights() -> None:
    """A valid portfolio keeps its order and normalizes symbols."""
    portfolio = Portfolio.model_validate(
        {"holdings": [{"symbol": "spy", "weight": 0.6}, {"symbol": "agg", "weight": 0.4}]}
    )

    assert portfolio.symbols == ["SPY", "AGG"]
    assert portfolio.weights == {"SPY": 0.6, "AGG": 0.4}


def test_single_holding_portfolio_is_allowed() -> None:
    """One asset at full weight is a valid portfolio."""
    assert Portfolio.model_validate({"holdings": [{"symbol": "SPY", "weight": 1}]}).symbols == [
        "SPY"
    ]


def test_weights_may_carry_rounding() -> None:
    """Thirds that sum to 1 only approximately are accepted."""
    third = 1 / 3
    holdings = [{"symbol": s, "weight": round(third, 7)} for s in ["A", "B"]]
    holdings.append({"symbol": "C", "weight": 1 - 2 * round(third, 7)})

    assert len(Portfolio.model_validate({"holdings": holdings}).holdings) == 3


@pytest.mark.parametrize(
    ("holdings", "message"),
    [
        ([{"symbol": "A", "weight": 0.5}, {"symbol": "B", "weight": 0.4}], "sum to 0.9"),
        ([{"symbol": "A", "weight": 1.0}, {"symbol": "B", "weight": 0.0}], "greater than 0"),
        ([{"symbol": "A", "weight": 1.5}, {"symbol": "B", "weight": -0.5}], "less than or equal"),
        ([{"symbol": "A", "weight": 0.5}, {"symbol": "a", "weight": 0.5}], "repeated: A"),
        ([], "at least 1"),
    ],
)
def test_portfolio_rejects_bad_holdings(holdings: list[dict[str, object]], message: str) -> None:
    """Weights off one, short or empty positions, and repeated symbols fail."""
    with pytest.raises(ValidationError, match=message):
        Portfolio.model_validate({"holdings": holdings})


# --- Downloads ---


def _serve(
    monkeypatch: pytest.MonkeyPatch, results: dict[str, pd.DataFrame | Exception]
) -> dict[str, int]:
    """Fake the price download per symbol; return the peak number of downloads in flight."""
    flight = {"now": 0, "peak": 0}

    async def fake_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
        flight["now"] += 1
        flight["peak"] = max(flight["peak"], flight["now"])
        await asyncio.sleep(0.01)
        flight["now"] -= 1
        result = results[symbol]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(panel_data, "fetch_price_history", fake_history)
    return flight


@pytest.mark.asyncio
async def test_closes_are_combined_in_request_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each symbol becomes a column, on the union of dates, in the order requested."""
    _serve(monkeypatch, {"MSFT": _bars([1, 2, 3]), "AAPL": _bars([4, 5], start="2026-01-06")})

    closes = (await fetch_close_panel(["MSFT", "AAPL"], "1y")).closes

    assert list(closes.columns) == ["MSFT", "AAPL"]
    assert isinstance(closes.index, pd.DatetimeIndex) and closes.index.tz is None
    assert len(closes) == 3
    assert np.isnan(closes["AAPL"].iloc[0])


@pytest.mark.asyncio
async def test_exchanges_line_up_by_their_own_calendar_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tokyo and New York bars of the same date share a row instead of shifting a day."""
    _serve(
        monkeypatch,
        {"SPY": _bars([1, 2, 3]), "7203.T": _bars([7, 8, 9], tz="Asia/Tokyo")},
    )

    panel = await fetch_close_panel(["SPY", "7203.T"])
    closes = panel.closes

    assert panel.timezones == {"SPY": "America/New_York", "7203.T": "Asia/Tokyo"}
    assert len(closes) == 3
    assert closes.notna().all().all()
    assert closes.index[0] == pd.Timestamp("2026-01-05")


@pytest.mark.asyncio
async def test_every_unknown_symbol_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown symbols are reported together, as not found."""
    _serve(
        monkeypatch,
        {
            "AAPL": _bars([1, 2]),
            "ZZZZ": SymbolNotFoundError("none"),
            "QQQQX": SymbolNotFoundError("none"),
            "MSFT": DataFetchError("down"),
        },
    )

    with pytest.raises(SymbolNotFoundError, match="Unknown ticker symbols: ZZZZ, QQQQX."):
        await fetch_close_panel(["AAPL", "ZZZZ", "MSFT", "QQQQX"])


@pytest.mark.asyncio
async def test_failed_downloads_are_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider failures name the symbols affected."""
    _serve(monkeypatch, {"AAPL": _bars([1, 2]), "MSFT": DataFetchError("down")})

    with pytest.raises(DataFetchError, match="for MSFT.") as caught:
        await fetch_close_panel(["AAPL", "MSFT"])
    assert not isinstance(caught.value, SymbolNotFoundError)


@pytest.mark.asyncio
async def test_downloads_run_concurrently_up_to_a_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Downloads overlap, but no more than the limit are in flight."""
    symbols = [f"S{i}" for i in range(MAX_SYMBOLS)]
    flight = _serve(monkeypatch, {symbol: _bars([1, 2]) for symbol in symbols})

    await fetch_close_panel(symbols)

    assert flight["peak"] == panel_data.MAX_CONCURRENT_DOWNLOADS


# --- Return panel ---


def test_returns_are_taken_after_aligning_dates() -> None:
    """A date one asset lacks is dropped for all, so the next return spans both days."""
    closes = _dated_closes({"A": [100.0, 110.0, 121.0, 133.1], "B": [50.0, 55.0, 60.5, 66.55]})
    closes.loc[closes.index[1], "B"] = np.nan

    panel = return_panel(closes, min_observations=2)

    assert panel.excluded_dates == 1
    assert panel.observations == 2
    assert panel.returns["A"].tolist() == pytest.approx([0.21, 0.10])
    assert panel.returns["B"].tolist() == pytest.approx([0.21, 0.10])


def test_time_zone_aware_closes_are_matched_by_date() -> None:
    """Closes stamped at exchange midnight align with plain dates."""
    closes = _dated_closes({"A": _walk(260, 1), "B": _walk(260, 2)})
    closes.index = pd.DatetimeIndex(closes.index).tz_localize("America/New_York")

    panel = return_panel(closes)

    assert isinstance(panel.returns.index, pd.DatetimeIndex)
    assert panel.returns.index.tz is None
    assert panel.observations == 259


def test_bad_prices_count_as_missing() -> None:
    """Zero, negative and non-finite closes are treated as no close that day."""
    closes = _dated_closes({"A": [100.0, 0.0, 102.0, 103.0], "B": [10.0, 11.0, -1.0, 13.0]})
    closes.loc[closes.index[3], "A"] = np.inf
    closes = pd.concat([closes, _dated_closes({"A": [104.0], "B": [14.0]}, start="2025-01-08")])

    panel = return_panel(closes, min_observations=1)

    assert panel.excluded_dates == 3
    assert panel.returns["A"].tolist() == pytest.approx([0.04])


def test_short_history_names_the_asset_and_its_start() -> None:
    """An asset with too few closes is named with the date its data begins."""
    closes = _dated_closes({"OLD": _walk(300, 1), "NEW": _walk(120, 2)})

    with pytest.raises(InsufficientDataError) as caught:
        return_panel(closes)

    message = str(caught.value)
    assert "NEW has 120 daily closes (from " in message
    assert "OLD" not in message


def test_late_listing_is_blamed_for_little_overlap() -> None:
    """When one asset's data starts much later, it is named as the cause."""
    closes = _dated_closes({"OLD": _walk(400, 1), "NEW": _walk(230, 2)})
    closes.loc[closes.index[-40:], "OLD"] = np.nan

    with pytest.raises(InsufficientDataError, match="NEW has the latest data"):
        return_panel(closes)


def test_mismatched_calendars_are_blamed_for_little_overlap() -> None:
    """When the assets start together, holidays in one market only are the cause."""
    closes = _dated_closes({"US": _walk(260, 1), "JP": _walk(260, 2)})
    closes.loc[closes.index[::6], "US"] = np.nan
    closes.loc[closes.index[3::6], "JP"] = np.nan

    with pytest.raises(InsufficientDataError, match="some of the markets were closed"):
        return_panel(closes)


def test_flat_prices_are_rejected_by_name() -> None:
    """An asset whose price never moves cannot be modeled and is named."""
    closes = _dated_closes({"A": _walk(260, 1), "CASH": [1.0] * 260})

    with pytest.raises(InsufficientDataError, match="never change over the window for CASH"):
        return_panel(closes)


# --- Leaving out what a fit cannot use ---


def test_short_and_flat_assets_are_left_out_and_the_rest_kept() -> None:
    """Too few closes and a price that never moves are reasons to leave an asset out."""
    closes = _dated_closes(
        {"A": _walk(300, 1), "B": _walk(300, 2), "NEW": _walk(120, 3), "CASH": [1.0] * 300}
    )

    panel, dropped = usable_return_panel(closes)

    assert list(panel.returns.columns) == ["A", "B"]
    assert panel.observations == 299
    assert [(d.symbol, d.reason) for d in dropped] == [
        ("NEW", "short_history"),
        ("CASH", "cash"),
    ]
    assert dropped[0].detail.startswith("120 daily closes, from ")


def test_a_late_start_is_left_out_when_it_leaves_the_rest_too_few_days() -> None:
    """A listing with enough closes of its own, but too few in common with one whose data
    stops early, is left out so the others keep their history."""
    closes = _dated_closes({"OLD": _walk(400, 1), "ALL": _walk(400, 2), "LATE": _walk(230, 3)})
    closes.loc[closes.index[-40:], "OLD"] = np.nan

    panel, dropped = usable_return_panel(closes)

    assert list(panel.returns.columns) == ["OLD", "ALL"]
    assert panel.observations == 359
    assert [(d.symbol, d.reason) for d in dropped] == [("LATE", "late_start")]
    assert "leaving the others only 189 common days" in dropped[0].detail


def test_nothing_usable_is_an_error_naming_every_asset() -> None:
    """When every asset is left out, the error says why for each."""
    closes = _dated_closes({"NEW": _walk(50, 1), "CASH": [1.0] * 300})

    with pytest.raises(InsufficientDataError, match="NEW: 50 daily closes.*CASH: its price"):
        usable_return_panel(closes)


@pytest.mark.asyncio
async def test_failed_downloads_can_be_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tolerating failures, the symbols that failed are named and the rest returned."""
    _serve(
        monkeypatch,
        {"AAPL": _bars([1, 2]), "ZZZZ": SymbolNotFoundError("nope"), "MSFT": _bars([3, 4])},
    )

    panel = await fetch_close_panel(["AAPL", "ZZZZ", "MSFT"], tolerate_failures=True)

    assert list(panel.closes.columns) == ["AAPL", "MSFT"]
    assert panel.failed == {"ZZZZ": "unknown symbol"}


@pytest.mark.asyncio
async def test_every_download_failing_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """With nothing downloaded, tolerating failures still raises."""
    _serve(monkeypatch, {"ZZZZ": SymbolNotFoundError("nope"), "YYYY": SymbolNotFoundError("no")})

    with pytest.raises(SymbolNotFoundError):
        await fetch_close_panel(["ZZZZ", "YYYY"], tolerate_failures=True)


def test_a_cutoff_leaves_out_starts_that_cost_too_much_history() -> None:
    """A late start cutting more than the allowed share off the others' history is left
    out; one within it is kept, and the window starts with it."""
    closes = _dated_closes({"OLD": _walk(1000, 1), "MID": _walk(800, 2), "NEW": _walk(300, 3)})

    panel, dropped = usable_return_panel(closes, max_history_cut=0.5)

    assert list(panel.returns.columns) == ["OLD", "MID"]
    assert panel.observations == 799
    assert [(d.symbol, d.reason) for d in dropped] == [("NEW", "late_start")]
    assert "cut 70% off the others' history, more than the 50% allowed" in dropped[0].detail


def test_without_a_cutoff_every_late_start_with_enough_history_is_kept() -> None:
    """The default keeps every holding that has enough closes of its own."""
    closes = _dated_closes({"OLD": _walk(1000, 1), "NEW": _walk(300, 3)})

    panel, dropped = usable_return_panel(closes)

    assert list(panel.returns.columns) == ["OLD", "NEW"] and dropped == []
    assert panel.observations == 299


def test_pairs_are_measured_over_the_history_they_share() -> None:
    """Each pair's own tau uses every day both traded, not just the panel's window."""
    closes = _dated_closes({"A": _walk(600, 1), "B": _walk(600, 2), "NEW": _walk(250, 3)})
    closes["B"] = closes["A"] * 1.5

    taus = pairwise_kendall(closes)

    assert taus[("A", "B")].observations == 599
    assert taus[("A", "B")].tau == pytest.approx(1.0)
    assert taus[("A", "NEW")].observations == 249
    assert set(taus) == {("A", "B"), ("A", "NEW"), ("B", "NEW")}
