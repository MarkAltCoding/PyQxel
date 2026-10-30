"""Runs a user's strategy on OHLCV bars and checks the positions it returns.

Loaded into Pyodide by the Python runner's Web Worker, and tested under CPython in
tests/test_strategy_harness.py. ``run_strategy`` takes and returns JSON strings, so
no Python objects cross into JavaScript.
"""

import contextlib
import io
import json
import linecache
import math
import traceback
from types import TracebackType
from typing import Any

import numpy as np
import pandas as pd

FILENAME = "<strategy>"
"""The name the user's code runs under, which marks its frames in tracebacks."""

MAX_POSITION = 2.0
"""Largest position, long or short, as a multiple of capital; the server checks it too."""

MAX_GROSS = 2.0
"""Largest sum of absolute weights across symbols on one bar; the server checks it too."""

FIELDS = (
    ("Open", "open"),
    ("High", "high"),
    ("Low", "low"),
    ("Close", "close"),
    ("Volume", "volume"),
)
"""Each bar column's name in the DataFrame, and its key in the payload."""

MAX_OUTPUT = 20_000
"""Most characters of printed output passed back."""


class StrategyError(Exception):
    """A strategy ran but returned positions that cannot be scored."""


def _bars(payload: dict[str, Any]) -> pd.DataFrame:
    """The bars as a DataFrame on a time-zone aware (UTC) index, oldest first."""
    index = pd.DatetimeIndex(pd.to_datetime(payload["timestamps"], utc=True), name="timestamp")
    return pd.DataFrame({name: _column(payload[key], index) for name, key in FIELDS}, index=index)


def _column(values: list[float | None], index: pd.Index) -> pd.Series:
    """One column of bar values as floats, NaN where missing."""
    return pd.to_numeric(pd.Series(values, index=index, dtype="object")).astype(float)


def _universe(payload: dict[str, Any]) -> pd.DataFrame:
    """Several symbols' bars on the dates they all traded, as one DataFrame whose columns
    are (field, symbol): ``bars["Close"]`` has a column per symbol."""
    index = pd.DatetimeIndex(pd.to_datetime(payload["dates"]), name="date")
    columns = pd.MultiIndex.from_product(
        [[name for name, _ in FIELDS], payload["symbols"]], names=["field", "symbol"]
    )
    data = {
        (name, symbol): _column(asset[key], index)
        for name, key in FIELDS
        for symbol, asset in zip(payload["symbols"], payload["assets"])
    }
    return pd.DataFrame(data, index=index, columns=columns)


def _weights(result: object, bars: pd.DataFrame, symbols: list[str]) -> list[list[float]]:
    """The weights in ``result``, one row per bar and one column per symbol, as floats;
    a symbol ``result`` has no column for gets a weight of zero.

    Raises:
        StrategyError: If ``result`` is not a finite weight per symbol and bar within range.
    """
    if isinstance(result, dict):
        result = pd.DataFrame(result)
    if not isinstance(result, pd.DataFrame):
        raise StrategyError(
            f"strategy() returned {type(result).__name__}; with several symbols, return a "
            "DataFrame of weights with one column per symbol, such as bars['Close'] * 0 + 0.5."
        )
    names = [str(column) for column in result.columns]
    extra = [name for name in names if name not in symbols]
    if extra:
        raise StrategyError(
            f"strategy() returned weights for {', '.join(extra)}, which it was not given; "
            f"its symbols are {', '.join(symbols)}."
        )
    # A symbol without a column is never held: its prices may be there only as a signal.
    frame = result.set_axis(names, axis=1).reindex(columns=symbols, fill_value=0.0)
    if isinstance(frame.index, pd.DatetimeIndex):
        frame = frame.reindex(bars.index)
    elif len(frame) == len(bars):
        frame = frame.set_axis(bars.index, axis=0)
    else:
        raise StrategyError(
            f"strategy() returned {len(frame)} rows of weights for {len(bars)} bars. Return "
            "a DataFrame on bars.index."
        )
    try:
        values = frame.apply(lambda column: pd.to_numeric(column.astype(object))).astype(float)
    except (TypeError, ValueError) as exc:
        raise StrategyError(f"Weights must be numbers: {exc}") from exc
    blank = values.isna()
    if blank.any().any():
        day = values.index[blank.any(axis=1).to_numpy()][0]
        symbol = blank.columns[blank.loc[day].to_numpy()][0]
        raise StrategyError(
            f"The weight of {symbol} is NaN on {day.date()}, and on {int(blank.sum().sum())} "
            "symbol-bars in all. Rolling windows are NaN until they fill: end with "
            ".fillna(0.0) to stay in cash until then."
        )
    numbers = values.to_numpy()
    outside = np.argwhere(~np.isfinite(numbers) | (np.abs(numbers) > MAX_POSITION))
    if len(outside):
        row, column = outside[0]
        raise StrategyError(
            f"Weights must be between -{MAX_POSITION:g} and {MAX_POSITION:g}; "
            f"{numbers[row, column]:g} for {symbols[column]} on {values.index[row].date()} "
            "is not."
        )
    gross = np.abs(numbers).sum(axis=1)
    over = np.flatnonzero(gross > MAX_GROSS + 1e-9)
    if len(over):
        raise StrategyError(
            f"The absolute weights may sum to at most {MAX_GROSS:g} on a bar; they sum to "
            f"{gross[over[0]]:g} on {values.index[over[0]].date()}. Divide by the number "
            "of symbols held to share capital between them."
        )
    return [[float(value) for value in row] for row in numbers]


def _positions(result: object, bars: pd.DataFrame) -> list[float]:
    """The positions in ``result``, one per bar, as floats.

    Raises:
        StrategyError: If ``result`` is not one finite position per bar within range.
    """
    if isinstance(result, pd.DataFrame):
        if result.shape[1] != 1:
            raise StrategyError(
                f"strategy() returned a DataFrame with {result.shape[1]} columns; return "
                "one Series of positions instead."
            )
        result = result.iloc[:, 0]
    if isinstance(result, pd.Series):
        if isinstance(result.index, pd.DatetimeIndex):
            series = result.reindex(bars.index)
        elif len(result) == len(bars):
            series = pd.Series(result.to_numpy(), index=bars.index)
        else:
            raise StrategyError(
                f"strategy() returned {len(result)} positions for {len(bars)} bars. Return "
                "a Series on bars.index."
            )
    elif isinstance(result, (np.ndarray, list, tuple)):
        if len(result) != len(bars):
            raise StrategyError(
                f"strategy() returned {len(result)} positions for {len(bars)} bars."
            )
        series = pd.Series(list(result), index=bars.index)
    else:
        raise StrategyError(
            f"strategy() returned {type(result).__name__}; return a pandas Series of "
            "positions on bars.index, such as 1.0 long, 0.0 flat and -1.0 short."
        )

    try:
        values = pd.to_numeric(series.astype(object)).astype(float)
    except (TypeError, ValueError) as exc:
        raise StrategyError(f"Positions must be numbers: {exc}") from exc
    missing = values.index[values.isna().to_numpy()]
    if len(missing):
        raise StrategyError(
            f"The position is NaN on {len(missing)} bars, the first on {missing[0].date()}. "
            "Rolling windows are NaN until they fill: end with .fillna(0.0) to stay flat "
            "until then."
        )
    numbers = values.to_numpy()
    outside = values.index[~np.isfinite(numbers) | (np.abs(numbers) > MAX_POSITION)]
    if len(outside):
        raise StrategyError(
            f"Positions must be between -{MAX_POSITION:g} and {MAX_POSITION:g}; "
            f"{values[outside[0]]:g} on {outside[0].date()} is not."
        )
    return [float(value) for value in values]


def _user_frames(tb: TracebackType | None) -> TracebackType | None:
    """``tb`` from the first frame of the user's code on, dropping the harness's own."""
    while tb is not None and tb.tb_frame.f_code.co_filename != FILENAME:
        tb = tb.tb_next
    return tb


def _line(exc: BaseException) -> int | None:
    """The line of the user's code where ``exc`` was raised, if it was."""
    if isinstance(exc, SyntaxError) and exc.filename == FILENAME:
        return exc.lineno
    line = None
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == FILENAME:
            line = tb.tb_lineno
        tb = tb.tb_next
    return line


def _failure(kind: str, exc: BaseException, output: str) -> dict[str, Any]:
    """An error result: ``exc``'s message, the user's line and their part of the traceback."""
    if isinstance(exc, StrategyError):
        trace = f"{exc}\n"
    else:
        # Without the exceptions it was raised from: pandas chains its internal KeyError
        # behind the one the user's code caused.
        trace = "".join(
            traceback.format_exception(type(exc), exc, _user_frames(exc.__traceback__), chain=False)
        )
    message = str(exc) if isinstance(exc, StrategyError) else f"{type(exc).__name__}: {exc}"
    return {
        "ok": False,
        "error": {"kind": kind, "message": message, "line": _line(exc), "traceback": trace},
        "output": output[-MAX_OUTPUT:],
    }


def run_strategy(code: str, payload_json: str) -> str:
    """Run ``code``'s ``strategy(bars)`` on the bars in ``payload_json``.

    Args:
        code: Python source defining ``strategy(bars: pd.DataFrame)``, returning a Series
            of positions for one symbol or a DataFrame of weights for several.
        payload_json: For one symbol, ``{"timestamps", "open", "high", "low", "close",
            "volume"}``, one list entry per bar, oldest first; missing values are null.
            For several, ``{"symbols", "dates", "assets"}``, with one such set of columns
            per symbol in ``assets``, on the dates they all traded.

    Returns:
        JSON of ``{"ok": true, "positions", "output"}``, with ``weights`` (a row per bar)
        in place of ``positions`` for several symbols, or ``{"ok": false, "error",
        "output"}`` where ``error`` has ``kind`` (``syntax``, ``runtime`` or ``result``),
        ``message``, ``line`` and ``traceback``.
    """
    linecache.cache[FILENAME] = (len(code), None, code.splitlines(keepends=True), FILENAME)
    payload = json.loads(payload_json)
    symbols: list[str] | None = payload.get("symbols")
    bars = _bars(payload) if symbols is None else _universe(payload)
    printed = io.StringIO()
    namespace: dict[str, Any] = {"__name__": "strategy", "np": np, "pd": pd, "math": math}
    with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
        try:
            compiled = compile(code, FILENAME, "exec")
        except SyntaxError as exc:
            return json.dumps(_failure("syntax", exc, printed.getvalue()))
        try:
            exec(compiled, namespace)
            strategy = namespace.get("strategy")
            if not callable(strategy):
                raise StrategyError(
                    "Define a function named strategy(bars) that returns a Series of positions, "
                    "or with several symbols a DataFrame of weights."
                )
            result = strategy(bars.copy())
        except StrategyError as exc:
            return json.dumps(_failure("result", exc, printed.getvalue()))
        except BaseException as exc:  # SystemExit too: exit() in user code is an error here.
            return json.dumps(_failure("runtime", exc, printed.getvalue()))
    output = printed.getvalue()[-MAX_OUTPUT:]
    try:
        if symbols is not None:
            weights = _weights(result, bars, symbols)
            return json.dumps({"ok": True, "weights": weights, "output": output})
        positions = _positions(result, bars)
    except StrategyError as exc:
        return json.dumps(_failure("result", exc, printed.getvalue()))
    return json.dumps({"ok": True, "positions": positions, "output": output})
