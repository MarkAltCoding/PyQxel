"""Per-user request rate limiting over fixed one-minute windows.

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


async def check_rate_limit(user_id: str, limit: int, now: float | None = None) -> None:
    """Count a request by ``user_id`` and refuse it beyond ``limit`` in this minute.

    Raises:
        RateLimitExceeded: If the user has already made ``limit`` requests this minute.
    """
    moment = time.time() if now is None else now
    window = int(moment // WINDOW_SECONDS)
    count = await cache_increment(f"ratelimit:{user_id}:{window}", 2 * WINDOW_SECONDS)
    if count is None:
        for stale in [key for key in _local if key[1] < window]:
            del _local[stale]
        count = _local.get((user_id, window), 0) + 1
        _local[(user_id, window)] = count
    if count > limit:
        retry_after = math.ceil((window + 1) * WINDOW_SECONDS - moment)
        raise RateLimitExceeded(retry_after=max(1, retry_after))
