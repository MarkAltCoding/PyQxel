"""The provider keys of the request being served.

PyQxel stores no provider keys: browsers keep them and send each one in its own header
with every request (:data:`PROVIDER_HEADERS`), and scripts do the same. Authentication
reads them once per request, and fetchers read them here, so every licensed request is
billed to the user who made it. Each request runs in its own context, and tasks and
threads it starts inherit it, so one user's keys are never visible while serving
another, and they are gone when the request ends.
"""

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass

from pydantic import SecretStr

PROVIDER_HEADERS: dict[str, str] = {
    "anthropic": "X-Anthropic-Key",
    "fmp": "X-FMP-Key",
    "alpha_vantage": "X-Alpha-Vantage-Key",
    "finnhub": "X-Finnhub-Key",
}
"""The header each provider's key is sent in."""

MIN_KEY_LENGTH: int = 8
MAX_KEY_LENGTH: int = 512


@dataclass(frozen=True)
class ProviderKeys:
    """The provider keys a request carried; ``None`` for each one it did not.

    ``yfinance`` is whether the request may fall back to yfinance: when the admin turned
    it on for everyone, or for themselves.
    """

    anthropic: SecretStr | None = None
    fmp: SecretStr | None = None
    alpha_vantage: SecretStr | None = None
    finnhub: SecretStr | None = None
    yfinance: bool = False


def _key(value: str | None) -> SecretStr | None:
    """A sent key, or ``None`` for a missing, blank or implausible one."""
    value = (value or "").strip()
    if not MIN_KEY_LENGTH <= len(value) <= MAX_KEY_LENGTH or not value.isprintable():
        return None
    return SecretStr(value)


def provider_keys_from(headers: Mapping[str, str], yfinance: bool = False) -> ProviderKeys:
    """The provider keys in a request's ``headers``."""
    return ProviderKeys(
        **{name: _key(headers.get(header)) for name, header in PROVIDER_HEADERS.items()},
        yfinance=yfinance,
    )


_current: ContextVar[ProviderKeys] = ContextVar("provider_keys", default=ProviderKeys())


def current_provider_keys() -> ProviderKeys:
    """The keys of the request being served; none outside a request."""
    return _current.get()


def use_provider_keys(keys: ProviderKeys) -> None:
    """Make ``keys`` the current request's provider keys."""
    _current.set(keys)
