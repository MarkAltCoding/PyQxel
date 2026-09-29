"""Bridge between Python and the embedded R session used by the statistical models.

R is single-threaded and not thread-safe, so every R call runs on one dedicated
worker thread. That serializes calls from concurrent requests and keeps blocking
model fits off the event loop. Call :func:`start_r` from the main thread at startup:
R claims the SIGINT handler when it starts, and only on the main thread can rpy2
hand it back to Python so Ctrl-C still stops the server. Otherwise the session
starts lazily on the first call. ``R_HOME`` from settings takes effect at start.

Scripts in ``r_scripts/`` are sourced once into R's global environment and their
functions called by name. Arguments and results are limited to plain vectors so
that rpy2 stays an implementation detail of this module.
"""

import asyncio
import logging
import math
import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np

from app.core.config import get_settings

logger = logging.getLogger(__name__)

R_SCRIPTS_DIR: Path = Path(__file__).resolve().parent / "r_scripts"

RArgument = str | int | float | bool | Sequence[float] | np.ndarray
"""Python values that convert to an R vector."""

RValue = list[str | int | float | bool | None]
"""An R vector read back into Python, with ``NA`` as ``None``."""

_executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="r-session")
_sourced_scripts: set[str] = set()


class RError(RuntimeError):
    """Raised when an R call fails."""


class RUnavailableError(RError):
    """Raised when R, rpy2, or a script's R packages cannot be loaded."""


def _robjects() -> ModuleType:
    """Import ``rpy2.robjects``, starting the embedded R session on first use."""
    r_home = get_settings().r_home
    if r_home is not None:
        os.environ["R_HOME"] = str(r_home)
    try:
        import rpy2.robjects as robjects
    except Exception as exc:
        raise RUnavailableError(
            "R is unavailable: install R and rpy2, and set R_HOME to the output of `R RHOME`."
        ) from exc
    return robjects


def _source(robjects: ModuleType, script: str) -> None:
    """Source ``r_scripts/<script>`` into the global environment once (R thread)."""
    if script in _sourced_scripts:
        return
    path = R_SCRIPTS_DIR / script
    try:
        robjects.r["source"](str(path))
    except Exception as exc:
        raise RUnavailableError(f"Could not load R script {script!r}: {exc}".strip()) from exc
    _sourced_scripts.add(script)


def _to_r(robjects: ModuleType, value: RArgument) -> Any:
    """Convert a Python scalar or numeric sequence to an R vector."""
    if isinstance(value, bool):
        return robjects.BoolVector([value])
    if isinstance(value, int):
        return robjects.IntVector([value])
    if isinstance(value, float):
        return robjects.FloatVector([value])
    if isinstance(value, str):
        return robjects.StrVector([value])
    return robjects.FloatVector(np.asarray(value, dtype=float))


def _from_r(robjects: ModuleType, vector: Any) -> RValue:
    """Read an atomic R vector into a list, mapping ``NA`` and ``NaN`` to ``None``."""
    values: RValue = []
    for item in vector:
        if item is robjects.NA_Logical or item is robjects.NA_Character:
            values.append(None)
        elif isinstance(item, float) and math.isnan(item):
            values.append(None)
        elif isinstance(item, bool | int | float | str):
            values.append(item)
        else:
            raise RError(f"Unsupported R value of type {type(item).__name__}.")
    return values


def _call(script: str, function: str, args: tuple[RArgument, ...]) -> dict[str, RValue]:
    """Source ``script`` and call ``function`` with ``args`` (blocking, R thread)."""
    robjects = _robjects()
    # rpy2 keeps conversion rules in a ContextVar that is only set in the thread that
    # imported it, so activate them explicitly here.
    with robjects.default_converter.context():
        _source(robjects, script)
        try:
            result = robjects.globalenv[function](*(_to_r(robjects, arg) for arg in args))
        except Exception as exc:
            raise RError(f"R function {function!r} failed: {exc}".strip()) from exc
        return {name: _from_r(robjects, result.rx2(name)) for name in result.names}


def start_r() -> bool:
    """Start the embedded R session from the calling thread, which should be the main one.

    Returns:
        ``True`` if R started, ``False`` if it is unavailable. Model calls then fail
        with :class:`RUnavailableError` rather than preventing the app from starting.
    """
    try:
        _robjects()
    except RUnavailableError:
        logger.warning("R is unavailable; statistical model endpoints will return 503.")
        return False
    return True


async def call_r(script: str, function: str, *args: RArgument) -> dict[str, RValue]:
    """Call an R function defined in ``r_scripts/<script>`` and return its named list.

    Args:
        script: File name inside ``r_scripts/``, e.g. ``"garch.R"``.
        function: Name of a function the script defines. It must return a named list
            of atomic vectors.
        *args: Positional arguments, each converted to an R vector.

    Returns:
        The list's elements by name, each as a Python list with ``NA`` as ``None``.

    Raises:
        RUnavailableError: If R, rpy2, or the script's packages cannot be loaded.
        RError: If the function raises in R or returns an unsupported value.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, partial(_call, script, function, args))
