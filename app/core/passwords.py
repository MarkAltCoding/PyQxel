"""Hashing and checking the passwords people sign in with.

Passwords are hashed with Argon2id, which takes deliberate time and memory for each
guess, so a stolen table of hashes is slow to crack. Each hash carries its own random
salt and the parameters it was made with, so the parameters can be raised later and old
hashes are upgraded the next time their owner signs in (:func:`needs_rehash`).
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_PASSWORD_LENGTH: int = 12
"""Fewest characters a password may have. Length matters more than mixing in digits and
symbols, so nothing else is required."""
MAX_PASSWORD_LENGTH: int = 256
"""Most characters a password may have, which bounds the work hashing one costs."""

_hasher = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)
"""Argon2id with OWASP's recommended minimum: 19 MiB of memory and two passes, light
enough for a small Cloud Run instance to check several sign-ins at once."""

_UNUSED_HASH: str = _hasher.hash("a password nobody has, checked for unknown emails")
"""Checked against when no account has the email, so a sign-in for an unknown address
takes as long as one for a known address and does not reveal which addresses have accounts."""


def password_problem(password: str, email: str) -> str | None:
    """Why ``password`` cannot be used for the account at ``email``, or ``None`` if it can."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Use at most {MAX_PASSWORD_LENGTH} characters."
    if password.strip().lower() == email.strip().lower():
        return "Use something other than your email address."
    if len(set(password)) < 4:
        return "Use a password that is not one character repeated."
    return None


def hash_password(password: str) -> str:
    """The Argon2id hash a password is stored as, with its salt and parameters."""
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Whether ``password`` matches ``stored_hash``.

    With no hash (an unknown email, or an account without a password) a fixed hash is
    checked anyway and ``False`` returned, so every answer takes about the same time.
    """
    try:
        matched = _hasher.verify(stored_hash or _UNUSED_HASH, password)
    except VerifyMismatchError, VerificationError, InvalidHashError:
        return False
    return matched and stored_hash is not None


def needs_rehash(stored_hash: str) -> bool:
    """Whether a hash was made with weaker parameters than today's and should be redone."""
    return _hasher.check_needs_rehash(stored_hash)
