"""The provider keys of the user a request is served for.

Authentication sets them once per request. Data fetchers read them here instead of from
the server's settings, so every licensed request is billed to the user who made it.
Each request runs in its own context, and tasks and threads it starts inherit it, so
one user's keys are never visible while serving another.
"""

from contextvars import ContextVar
from dataclasses import dataclass

from pydantic import SecretStr


@dataclass(frozen=True)
class ProviderKeys:
    """API keys for paid data providers; ``None`` when the user has not stored one."""

    fmp: SecretStr | None = None


_current: ContextVar[ProviderKeys] = ContextVar("provider_keys", default=ProviderKeys())


def current_provider_keys() -> ProviderKeys:
    """The keys of the user the current request is served for; none outside a request."""
    return _current.get()


def use_provider_keys(keys: ProviderKeys) -> None:
    """Make ``keys`` the current request's provider keys."""
    _current.set(keys)
