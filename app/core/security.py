"""API keys that identify users.

PyQxel API keys are random tokens shown to their owner once; only their SHA-256 hash is
stored, which suffices for tokens this long. Provider keys are not stored at all:
browsers keep them and send them with each request.
"""

import hashlib
import hmac
import secrets

API_KEY_PREFIX: str = "pq_"
"""Marks PyQxel API keys, so they are recognizable in configuration and secret scanners."""

API_KEY_DISPLAY_LENGTH: int = 10
"""Leading characters of a key kept to tell keys apart in listings."""


def generate_api_key() -> str:
    """A new PyQxel API key: the prefix and 256 random bits."""
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    """The SHA-256 hex digest an API key is stored and looked up by."""
    return hashlib.sha256(key.encode()).hexdigest()


def same_hash(first: str, second: str) -> bool:
    """Compare two digests in constant time."""
    return hmac.compare_digest(first, second)


def key_hint(secret: str) -> str:
    """The last four characters of a key, for recognizing it without revealing it."""
    return "…" + secret[-4:] if len(secret) > 8 else "…"
