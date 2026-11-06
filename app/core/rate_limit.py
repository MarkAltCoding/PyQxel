"""Request rate limiting over fixed windows, one minute long unless a caller asks for
longer.

Counts live in Redis when ``REDIS_URL`` is set, so the limit holds across worker
processes; otherwise, or while Redis is down, each process counts on its own.
"""

import math
import time
from dataclasses import dataclass

from app.core.cache import cache_increment

WINDOW_SECONDS: float = 60.0

_local: dict[tuple[str, int], int] = {}
"""Counts per (user, window) when Redis is unavailable."""


@dataclass(frozen=True)
class RateLimitExceeded(Exception):
    """Raised when a user has used up the current window's requests."""

    retry_after: int
    """Seconds until the next window opens."""


def clear_local_counts() -> None:
    """Forget in-process counts."""
    _local.clear()


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
    count = await cache_increment(f"ratelimit:{name}:{window}", 2 * window_seconds)
    if count is None:
        for stale in [key for key in _local if key[0] == name and key[1] < window]:
            del _local[stale]
        count = _local.get((name, window), 0) + 1
        _local[(name, window)] = count
    if count > limit:
        retry_after = math.ceil((window + 1) * window_seconds - moment)
        raise RateLimitExceeded(retry_after=max(1, retry_after))
