"""Tests for the Python-R bridge.

Conversion and error tests run a small script in the real embedded R session and
are skipped when R or rpy2 is unavailable.
"""

from pathlib import Path

import pytest

from app.stats import r_bridge
from app.stats.r_bridge import RError, RUnavailableError, call_r, start_r


def _r_available() -> bool:
    """Report whether the embedded R session can start."""
    try:
        r_bridge._robjects()
    except RUnavailableError:
        return False
    return True


needs_r = pytest.mark.skipif(not _r_available(), reason="R and rpy2 are not installed")

SCRIPT = """
pyqxel_test_echo <- function(numbers, flag, count, label) {
  list(
    doubled = numbers * 2,
    with_na = c(1.5, NA, NaN),
    flag = flag,
    count = count + 1L,
    label = paste0(label, "!"),
    missing_text = NA_character_
  )
}
pyqxel_test_fail <- function() stop("deliberate failure")
"""


@pytest.fixture
def scripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the bridge at a temporary scripts directory holding the test script."""
    (tmp_path / "bridge_test.R").write_text(SCRIPT)
    monkeypatch.setattr(r_bridge, "R_SCRIPTS_DIR", tmp_path)
    return tmp_path


def test_start_r_reports_missing_r(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without R, startup reports failure instead of raising."""

    def unavailable() -> None:
        raise RUnavailableError("no R")

    monkeypatch.setattr(r_bridge, "_robjects", unavailable)

    assert start_r() is False


@needs_r
def test_start_r_with_r() -> None:
    """With R installed, the session starts."""
    assert start_r() is True


@needs_r
@pytest.mark.asyncio
async def test_values_round_trip_through_r(scripts: Path) -> None:
    """Numbers, booleans, integers and strings convert both ways, with NA and NaN as None."""
    result = await call_r("bridge_test.R", "pyqxel_test_echo", [1.0, 2.5], True, 4, "hi")

    assert result["doubled"] == [2.0, 5.0]
    assert result["with_na"] == [1.5, None, None]
    assert result["flag"] == [True]
    assert result["count"] == [5]
    assert result["label"] == ["hi!"]
    assert result["missing_text"] == [None]


@needs_r
@pytest.mark.asyncio
async def test_r_errors_are_wrapped(scripts: Path) -> None:
    """An error raised inside R becomes an RError naming the function."""
    with pytest.raises(RError, match="pyqxel_test_fail"):
        await call_r("bridge_test.R", "pyqxel_test_fail")


@needs_r
@pytest.mark.asyncio
async def test_missing_script_is_unavailable(scripts: Path) -> None:
    """A script that cannot be sourced makes the model unavailable, not a crash."""
    with pytest.raises(RUnavailableError, match="no_such_script.R"):
        await call_r("no_such_script.R", "anything")
