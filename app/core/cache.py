"""Optional Redis cache shared by every worker process.

Caching is enabled by setting ``REDIS_URL``. Without it, or while Redis is
unreachable, every lookup is a miss and every store is skipped, so callers fall
through to their data provider. After a Redis error the cache stays off for
:data:`RETRY_AFTER_SECONDS` rather than making each request wait out a timeout.

Values are stored as JSON text; callers serialize and parse their own types.
"""

import logging
import time

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

KEY_PREFIX: str = "pyqxel:"
"""Namespace for every key this app writes, so a shared Redis is safe to use."""

RETRY_AFTER_SECONDS: float = 30.0
"""How long the cache stays off after a Redis error."""

SOCKET_TIMEOUT_SECONDS: float = 0.5
"""Connect and read timeout, kept short because a slow cache is worse than none."""

_client: Redis | None = None
_configured: bool = False
_disabled_until: float = 0.0


def configure_cache(client: Redis | None) -> None:
    """Use ``client`` for caching, or disable caching when ``None``.

    Replaces any client created from settings; the previous client is not closed.
    """
    global _client, _configured, _disabled_until
    _client = client
    _configured = True
    _disabled_until = 0.0


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
                socket_connect_timeout=SOCKET_TIMEOUT_SECONDS,
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
    """Return the text cached under ``key``, or ``None`` on a miss or when caching is off."""
    client = _get_client()
    if client is None:
        return None
    try:
        value = await client.get(KEY_PREFIX + key)
    except RedisError as exc:
        _trip("read", key, exc)
        return None
    return value if isinstance(value, str) or value is None else value.decode()


async def cache_set(key: str, value: str, ttl_seconds: float) -> None:
    """Cache ``value`` under ``key`` for ``ttl_seconds``; a no-op when caching is off."""
    client = _get_client()
    if client is None:
        return
    try:
        await client.set(KEY_PREFIX + key, value, px=max(1, round(ttl_seconds * 1000)))
    except RedisError as exc:
        _trip("write", key, exc)


async def cache_increment(key: str, ttl_seconds: float) -> int | None:
    """Add one to the counter at ``key``, starting its ``ttl_seconds`` expiry when it is new.

    Returns:
        The new count, or ``None`` when caching is off or Redis fails.
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


async def close_cache() -> None:
    """Close the Redis connection pool, if one was opened."""
    global _client, _configured
    if _client is not None:
        await _client.aclose()
    _client = None
    _configured = False
