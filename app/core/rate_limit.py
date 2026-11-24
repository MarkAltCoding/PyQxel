"""Request rate limiting over fixed windows, one minute long unless a caller asks for
longer.

Each process counts in its own memory. Sharing the counts through Redis would make the
limit exact across instances, but at four Redis commands on every request it cost more
than it is worth, with two instances at most: an account could at worst make twice the
limit while both run.
"""

import math
import time
from dataclasses import dataclass

WINDOW_SECONDS: float = 60.0

_local: dict[tuple[str, int], int] = {}
"""Counts per (user, window)."""

_ends: dict[tuple[str, int], float] = {}
"""When each counted window ends, so a sweep can drop the ones that have."""

SWEEP_SECONDS: float = 60.0
"""How often counts of windows that have ended are dropped.

Without it, every client address that ever tried to sign in would keep its count in
memory for as long as the process runs, which an attacker sending from many addresses
could grow without bound."""

_last_sweep: list[float] = [0.0]


def _sweep(moment: float) -> None:
    """Drop the counts of every window that has ended, at most once a minute."""
    if moment - _last_sweep[0] < SWEEP_SECONDS:
        return
    _last_sweep[0] = moment
    for key in [key for key, end in _ends.items() if end <= moment]:
        _ends.pop(key, None)
        _local.pop(key, None)


@dataclass(frozen=True)
class RateLimitExceeded(Exception):
    """Raised when a user has used up the current window's requests."""

    retry_after: int
    """Seconds until the next window opens."""


def clear_local_counts() -> None:
    """Forget in-process counts."""
    _local.clear()
    _ends.clear()
    _last_sweep[0] = 0.0


async def check_rate_limit(
    user_id: str,
    limit: int,
    now: float | None = None,
    window_seconds: float = WINDOW_SECONDS,
) -> None:
    """Count a request by ``user_id`` and refuse it beyond ``limit`` in this window.

    ``window_seconds`` sets the window's length, a minute by default; a longer one, as
    for password attempts on one account, counts under its own name.

    Raises:
        RateLimitExceeded: If the user has already made ``limit`` requests this window.
    """
    moment = time.time() if now is None else now
    name = user_id if window_seconds == WINDOW_SECONDS else f"{user_id}:{int(window_seconds)}s"
    window = int(moment // window_seconds)
    _sweep(moment)
    _local.pop((name, window - 1), None)
    count = _local.get((name, window), 0) + 1
    _local[(name, window)] = count
    _ends[(name, window)] = (window + 1) * window_seconds
    if count > limit:
        retry_after = math.ceil((window + 1) * window_seconds - moment)
        raise RateLimitExceeded(retry_after=max(1, retry_after))
