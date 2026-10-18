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

MAX_OUTPUT = 20_000
"""Most characters of printed output passed back."""


class StrategyError(Exception):
    """A strategy ran but returned positions that cannot be scored."""


def _bars(payload: dict[str, Any]) -> pd.DataFrame:
    """The bars as a DataFrame on a time-zone aware (UTC) index, oldest first."""
    index = pd.DatetimeIndex(pd.to_datetime(payload["timestamps"], utc=True), name="timestamp")
    columns = {
        name: pd.to_numeric(pd.Series(payload[key], index=index, dtype="object")).astype(float)
        for name, key in (
            ("Open", "open"),
            ("High", "high"),
            ("Low", "low"),
            ("Close", "close"),
            ("Volume", "volume"),
        )
    }
    return pd.DataFrame(columns, index=index)


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
        code: Python source defining ``strategy(bars: pd.DataFrame) -> pd.Series``.
        payload_json: ``{"timestamps", "open", "high", "low", "close", "volume"}``, one
            list entry per bar, oldest first; missing values are null.

    Returns:
        JSON of ``{"ok": true, "positions", "output"}``, or ``{"ok": false, "error",
        "output"}`` where ``error`` has ``kind`` (``syntax``, ``runtime`` or ``result``),
        ``message``, ``line`` and ``traceback``.
    """
    linecache.cache[FILENAME] = (len(code), None, code.splitlines(keepends=True), FILENAME)
    bars = _bars(json.loads(payload_json))
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
                    "Define a function named strategy(bars) that returns a Series of positions."
                )
            result = strategy(bars.copy())
        except StrategyError as exc:
            return json.dumps(_failure("result", exc, printed.getvalue()))
        except BaseException as exc:  # SystemExit too: exit() in user code is an error here.
            return json.dumps(_failure("runtime", exc, printed.getvalue()))
    try:
        positions = _positions(result, bars)
    except StrategyError as exc:
        return json.dumps(_failure("result", exc, printed.getvalue()))
    return json.dumps(
        {"ok": True, "positions": positions, "output": printed.getvalue()[-MAX_OUTPUT:]}
    )
