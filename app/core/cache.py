"""The cache of data fetched from providers: in this process's memory, and in Redis too
when ``REDIS_URL`` is set.

Every value is kept in memory, up to :data:`MEMORY_MAX_BYTES`, the least recently used
leaving first, so one instance serves a repeated lookup without asking its provider again
whether or not Redis is there. Redis, e.g. an Upstash ``rediss://`` URL, is optional: it
shares the cache, and each Finnhub key's count of requests a minute, among instances and
keeps them across restarts, so instances fetch less between them. Reads look in memory
first, so Redis is asked only for what this instance has not kept. Without
it, or while Redis is unreachable, the memory alone serves. After a Redis error, Redis is
left out for :data:`RETRY_AFTER_SECONDS` rather than making each request wait out a timeout.

Hosted Redis such as Upstash closes connections left idle, as they are whenever traffic
pauses. Idle connections are checked before reuse, and a command that finds its
connection closed is retried once on a new one, so a quiet spell does not pause the cache.

Values are stored as JSON text; callers serialize and parse their own types.
"""

import logging
import time
from collections import OrderedDict
from typing import Literal

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

KEY_PREFIX: str = "pyqxel:"
"""Namespace for every key this app writes, so a shared Redis is safe to use."""

RETRY_AFTER_SECONDS: float = 30.0
"""How long the cache stays off after a Redis error."""

SOCKET_TIMEOUT_SECONDS: float = 0.5
"""Read timeout, kept short because a slow cache is worse than none."""

CONNECT_TIMEOUT_SECONDS: float = 1.5
"""Connect timeout, long enough for a TLS handshake from a newly started instance."""

HEALTH_CHECK_INTERVAL_SECONDS: int = 30
"""Connections idle longer than this are pinged before reuse."""

MEMORY_MAX_BYTES: int = 192 * 1024 * 1024
"""Most text the memory tier holds, in characters: a small share of an instance's 2 GiB,
room for thousands of price histories and statements."""

_memory: OrderedDict[str, tuple[float, str]] = OrderedDict()
"""Key -> (when it expires, by the monotonic clock; the text), least recently used first."""

_memory_size: int = 0

_memory_on: bool = True
"""Whether the memory tier is used; only tests turn it off."""


def _remember(key: str, value: str, ttl_seconds: float) -> None:
    """Keep ``value`` in memory for ``ttl_seconds``, making room by dropping the least
    recently used; a value larger than the whole tier is not kept."""
    global _memory_size
    _forget(key)
    if not _memory_on or len(value) > MEMORY_MAX_BYTES:
        return
    _memory[key] = (time.monotonic() + ttl_seconds, value)
    _memory_size += len(value)
    while _memory_size > MEMORY_MAX_BYTES and _memory:
        _, (_, dropped) = _memory.popitem(last=False)
        _memory_size -= len(dropped)


def _forget(key: str) -> None:
    global _memory_size
    old = _memory.pop(key, None)
    if old is not None:
        _memory_size -= len(old[1])


def _recall(key: str) -> str | None:
    """The text kept in memory under ``key``, or ``None`` when missing or expired."""
    found = _memory.get(key)
    if found is None:
        return None
    if found[0] <= time.monotonic():
        _forget(key)
        return None
    _memory.move_to_end(key)
    return found[1]


def clear_memory_cache() -> None:
    """Forget everything kept in memory, for tests."""
    global _memory_size
    _memory.clear()
    _memory_size = 0


_client: Redis | None = None
_configured: bool = False
_disabled_until: float = 0.0


def configure_cache(client: Redis | None, memory: bool = False) -> None:
    """Use ``client`` for caching, or no Redis when ``None``, and the memory tier only
    with ``memory``: tests configure the cache this way, and see exactly the caching
    they ask for. The app itself configures from settings, with the memory tier on.

    Replaces any client created from settings; the previous client is not closed.
    """
    global _client, _configured, _disabled_until, _memory_on
    _client = client
    _configured = True
    _disabled_until = 0.0
    _memory_on = memory
    clear_memory_cache()


def _get_client() -> Redis | None:
    """Return the Redis client, creating it from ``REDIS_URL`` on first use."""
    global _client, _configured
    if not _configured:
        url = get_settings().redis_url
        _client = (
            None
            if url is None
            else Redis.from_url(
                url.get_secret_value(),
                socket_timeout=SOCKET_TIMEOUT_SECONDS,
                socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
                health_check_interval=HEALTH_CHECK_INTERVAL_SECONDS,
                retry=Retry(NoBackoff(), retries=1),
                retry_on_error=[RedisConnectionError, RedisTimeoutError],
                decode_responses=True,
            )
        )
        _configured = True
    if _client is None or time.monotonic() < _disabled_until:
        return None
    return _client


def _trip(operation: str, key: str, exc: RedisError) -> None:
    """Log a Redis failure and turn the cache off for :data:`RETRY_AFTER_SECONDS`."""
    global _disabled_until
    _disabled_until = time.monotonic() + RETRY_AFTER_SECONDS
    logger.warning(
        "Redis %s failed for %s; caching paused for %.0fs: %s",
        operation,
        key,
        RETRY_AFTER_SECONDS,
        exc,
    )


async def cache_get(key: str) -> str | None:
    """Return the text cached under ``key``, from memory, else Redis (kept in memory for
    a minute after), or ``None`` on a miss."""
    found = _recall(key)
    if found is not None:
        return found
    client = _get_client()
    if client is None:
        return None
    try:
        value = await client.get(KEY_PREFIX + key)
    except RedisError as exc:
        _trip("read", key, exc)
        return None
    text = value if isinstance(value, str) or value is None else value.decode()
    if text is not None:
        # Its remaining lifetime is Redis's to know; a minute keeps repeats local.
        _remember(key, text, SHARED_RECALL_SECONDS)
    return text


SHARED_RECALL_SECONDS: float = 60.0
"""How long a value found in Redis is also kept in memory."""


async def cache_set(key: str, value: str, ttl_seconds: float) -> None:
    """Cache ``value`` under ``key`` for ``ttl_seconds``, in memory and, when it is set,
    in Redis."""
    _remember(key, value, ttl_seconds)
    client = _get_client()
    if client is None:
        return
    try:
        await client.set(KEY_PREFIX + key, value, px=max(1, round(ttl_seconds * 1000)))
    except RedisError as exc:
        _trip("write", key, exc)


async def cache_increment(key: str, ttl_seconds: float) -> int | None:
    """Add one to the shared counter at ``key``, which expires ``ttl_seconds`` after its
    first count: one Redis command a count, and a second on the first, which sets the
    expiry. Counters are keyed by time window, so an expiry from the first count suits
    them; should a process stop between the two, the counter outlives its window, and
    Redis's eviction clears it.

    Returns:
        The new count, or ``None`` when Redis is not set or fails.
    """
    client = _get_client()
    if client is None:
        return None
    try:
        count = int(await client.incr(KEY_PREFIX + key))
        if count == 1:
            await client.pexpire(KEY_PREFIX + key, max(1, round(ttl_seconds * 1000)))
    except RedisError as exc:
        _trip("count", key, exc)
        return None
    return count


async def check_cache() -> Literal["ok", "unavailable", "not_configured"]:
    """Ping Redis for a health check.

    Returns:
        ``"not_configured"`` without ``REDIS_URL``, else whether Redis answered. The
        ping is sent even while caching is paused after an error.
    """
    _get_client()
    if _client is None:
        return "not_configured"
    try:
        await _client.ping()
    except RedisError as exc:
        logger.warning("Redis health check failed: %s", exc)
        return "unavailable"
    return "ok"


async def close_cache() -> None:
    """Close the Redis connection pool, if one was opened; a Redis that is down or out of
    its plan's commands does not stop the app from shutting down."""
    global _client, _configured
    if _client is not None:
        try:
            await _client.aclose()
        except (RedisError, OSError) as exc:
            logger.warning("Redis could not be closed cleanly: %s", exc)
    _client = None
    _configured = False
