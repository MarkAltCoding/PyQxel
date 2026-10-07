"""API keys that identify users, and encryption of the provider keys users store.

PyQxel API keys are random tokens shown to their owner once; only their SHA-256 hash is
stored, which suffices for tokens this long. Provider keys (Anthropic, Financial
Modeling Prep) must be used, not just checked, so they are encrypted with Fernet under
``CREDENTIALS_ENCRYPTION_KEY`` instead.
"""

import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from app.core.config import get_settings

API_KEY_PREFIX: str = "pq_"
"""Marks PyQxel API keys, so they are recognizable in configuration and secret scanners."""

API_KEY_DISPLAY_LENGTH: int = 10
"""Leading characters of a key kept to tell keys apart in listings."""


class CredentialsUnavailableError(RuntimeError):
    """Raised when provider keys cannot be stored or read: no or a wrong encryption key."""


def generate_api_key() -> str:
    """A new PyQxel API key: the prefix and 256 random bits."""
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    """The SHA-256 hex digest an API key is stored and looked up by."""
    return hashlib.sha256(key.encode()).hexdigest()


def same_hash(first: str, second: str) -> bool:
    """Compare two digests in constant time."""
    return hmac.compare_digest(first, second)


def generate_encryption_key() -> str:
    """A new Fernet key for ``CREDENTIALS_ENCRYPTION_KEY``."""
    return Fernet.generate_key().decode()


def _fernet() -> Fernet:
    key = get_settings().credentials_encryption_key
    if key is None:
        raise CredentialsUnavailableError(
            "The server cannot store provider keys: CREDENTIALS_ENCRYPTION_KEY is not set."
        )
    try:
        return Fernet(key.get_secret_value().encode())
    except ValueError as exc:
        raise CredentialsUnavailableError(
            "CREDENTIALS_ENCRYPTION_KEY is not a valid Fernet key."
        ) from exc


def encrypt_secret(secret: str) -> str:
    """Encrypt a provider key for storage."""
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token: str) -> SecretStr:
    """Decrypt a stored provider key.

    Raises:
        CredentialsUnavailableError: If the encryption key is missing or is not the one
            the value was encrypted with.
    """
    try:
        return SecretStr(_fernet().decrypt(token.encode()).decode())
    except InvalidToken as exc:
        raise CredentialsUnavailableError(
            "A stored provider key cannot be decrypted; CREDENTIALS_ENCRYPTION_KEY has changed."
        ) from exc


def key_hint(secret: str) -> str:
    """The last four characters of a key, for recognizing it without revealing it."""
    return "…" + secret[-4:] if len(secret) > 8 else "…"
