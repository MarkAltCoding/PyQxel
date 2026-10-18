"""Tests for the harness that runs custom Python strategies in the browser.

The harness runs on Pyodide in a Web Worker (web/src/runners/python/), which has the
same NumPy and pandas; here it runs under CPython, which is what CI has.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pytest

HARNESS = Path(__file__).parents[1] / "web" / "src" / "runners" / "python" / "harness.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("strategy_harness", HARNESS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness = _load()

STAMPS = [
    stamp.isoformat() for stamp in pd.bdate_range("2025-01-02", periods=60, tz="America/New_York")
]
"""Bar timestamps as the history endpoint sends them: New York time, in EST."""


def _payload(**columns: list[Any]) -> str:
    """Sixty bars rising by one a day, with ``columns`` replacing any of them."""
    closes = [100.0 + bar for bar in range(len(STAMPS))]
    payload: dict[str, Any] = {
        "timestamps": STAMPS,
        "open": closes,
        "high": [close + 1 for close in closes],
        "low": [close - 1 for close in closes],
        "close": closes,
        "volume": [1_000 + bar for bar in range(len(STAMPS))],
    }
    return json.dumps(payload | columns)


def _run(code: str, payload: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(harness.run_strategy(code, payload or _payload()))
    return result


def test_strategy_gets_ohlcv_bars_on_an_aware_index() -> None:
    """The bars have every OHLCV column as floats, missing values as NaN, on UTC time."""
    code = """
def strategy(bars):
    print(list(bars.columns), str(bars.index.tz), bars["Low"].isna().sum(), bars.dtypes.unique())
    return bars["Close"] * 0 + 1
"""
    low: list[float | None] = [99.0] * len(STAMPS)
    low[3] = None

    result = _run(code, _payload(low=low))

    assert result["ok"] is True
    assert result["positions"] == [1.0] * len(STAMPS)
    assert result["output"].startswith("['Open', 'High', 'Low', 'Close', 'Volume'] UTC 1")
    assert "float64" in result["output"]


def test_moving_average_crossover_positions() -> None:
    """A strategy's Series comes back one float per bar, in order."""
    code = """
def strategy(bars):
    slow = bars["Close"].rolling(20).mean()
    return (bars["Close"] > slow).astype(float).where(slow.notna(), 0.0)
"""
    result = _run(code)

    assert result["positions"] == [0.0] * 19 + [1.0] * (len(STAMPS) - 19)


@pytest.mark.parametrize(
    "returns",
    [
        "[1.0] * len(bars)",
        "np.ones(len(bars))",
        "pd.Series(True, index=bars.index)",
        "(bars['Close'] * 0 + 1).reset_index(drop=True)",
        "(bars['Close'] * 0 + 1).to_frame()",
    ],
)
def test_lists_arrays_booleans_and_one_column_frames_are_positions(returns: str) -> None:
    """Anything with one number per bar is accepted, as is True for fully long."""
    result = _run(f"def strategy(bars):\n    return {returns}\n")

    assert result == {"ok": True, "positions": [1.0] * len(STAMPS), "output": ""}


def test_nan_positions_suggest_fillna() -> None:
    """NaN from an unfilled rolling window is an error naming the first bar and the fix."""
    result = _run("def strategy(bars):\n    return bars['Close'].rolling(5).mean() * 0 + 1\n")

    assert result["ok"] is False
    assert result["error"]["kind"] == "result"
    assert "NaN on 4 bars, the first on 2025-01-02" in result["error"]["message"]
    assert ".fillna(0.0)" in result["error"]["message"]


def test_a_series_missing_bars_is_nan_on_them() -> None:
    """A Series on part of bars.index leaves the other bars without a position."""
    result = _run("def strategy(bars):\n    return (bars['Close'] * 0 + 1).iloc[10:]\n")

    assert "NaN on 10 bars" in result["error"]["message"]


@pytest.mark.parametrize(
    ("returns", "message"),
    [
        ("bars['Close'] * 0 + 3", "between -2 and 2; 3 on 2025-01-02"),
        ("bars['Close'] * 0 - 2.5", "-2.5 on 2025-01-02"),
        ("[1.0, 0.0]", "returned 2 positions for 60 bars"),
        ("bars", "DataFrame with 5 columns"),
        ("'long'", "returned str"),
        ("None", "returned NoneType"),
        ("pd.Series(['a'] * len(bars), index=bars.index)", "Positions must be numbers"),
    ],
)
def test_positions_that_cannot_be_scored_are_errors(returns: str, message: str) -> None:
    """Out-of-range, wrongly sized and non-numeric results are explained."""
    result = _run(f"def strategy(bars):\n    return {returns}\n")

    assert result["ok"] is False
    assert result["error"]["kind"] == "result"
    assert message in result["error"]["message"]


def test_runtime_errors_point_at_the_users_line() -> None:
    """The traceback starts in the user's code, without the harness's frames, and ``line``
    is the deepest line of the user's code that raised."""
    code = (
        "def helper(bars):\n    return bars['Nope']\n\n\n"
        "def strategy(bars):\n    return helper(bars)\n"
    )

    result = _run(code)

    error = result["error"]
    assert (error["kind"], error["message"], error["line"]) == ("runtime", "KeyError: 'Nope'", 2)
    assert error["traceback"].startswith('Traceback (most recent call last):\n  File "<strategy>"')
    assert "return helper(bars)" in error["traceback"]
    assert "harness.py" not in error["traceback"]
    assert "direct cause" not in error["traceback"]


def test_syntax_errors_point_at_the_line() -> None:
    result = _run("x = 1\ndef strategy(bars)\n    return 1\n")

    error = result["error"]
    assert (error["kind"], error["line"]) == ("syntax", 2)
    assert "expected ':'" in error["message"]


@pytest.mark.parametrize(
    "code", ["x = 1\n", "strategy = 5\n", "def strategize(bars):\n    return 1\n"]
)
def test_code_without_a_strategy_function_is_an_error(code: str) -> None:
    result = _run(code)

    assert result["error"]["kind"] == "result"
    assert "Define a function named strategy(bars)" in result["error"]["message"]


def test_exit_is_an_error_not_an_escape() -> None:
    """exit() inside a strategy is reported like any other exception."""
    result = _run("def strategy(bars):\n    exit(3)\n")

    assert (result["error"]["kind"], result["error"]["message"]) == ("runtime", "SystemExit: 3")


def test_printed_output_is_returned_and_capped() -> None:
    """Prints come back with the result, and with errors, trimmed to the last 20,000 characters."""
    code = "def strategy(bars):\n    print('x' * 30_000)\n    print('done')\n    return 1 / 0\n"

    result = _run(code)

    assert result["error"]["message"] == "ZeroDivisionError: division by zero"
    assert len(result["output"]) == harness.MAX_OUTPUT
    assert result["output"].endswith("done\n")


def test_strategies_get_a_copy_of_the_bars() -> None:
    """Changing the bars inside a strategy cannot change the positions checked against them."""
    code = "def strategy(bars):\n    bars.drop(bars.index, inplace=True)\n    return [1.0] * 60\n"

    assert _run(code)["ok"] is True


STARTER = Path(__file__).parents[1] / "web" / "src" / "editor" / "starters" / "strategy.py"


def test_the_editors_starter_strategy_runs() -> None:
    """The code a new strategy starts from runs as written and trades on a trending series."""
    days = pd.bdate_range("2023-01-02", periods=400, tz="America/New_York")
    closes = [100.0 * (1.0005**bar) * (1.0 + 0.02 * ((bar % 7) - 3) / 3) for bar in range(400)]
    payload = json.dumps(
        {
            "timestamps": [stamp.isoformat() for stamp in days],
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1_000] * 400,
        }
    )

    result = _run(STARTER.read_text(encoding="utf-8"), payload)

    assert result["ok"] is True, result
    positions = result["positions"]
    assert positions[:199] == [0.0] * 199
    assert 0.0 < max(positions) <= 1.0
