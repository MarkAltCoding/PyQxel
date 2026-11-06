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
    "tiingo": "X-Tiingo-Key",
    "eodhd": "X-EODHD-Key",
}
"""The header each provider's key is sent in."""

MIN_KEY_LENGTH: int = 8
MAX_KEY_LENGTH: int = 512


@dataclass(frozen=True)
class ProviderKeys:
    """The provider keys a request carried; ``None`` for each one it did not.

    ``yfinance`` is whether the request may fall back to yfinance, and ``unlicensed``
    whether it may be shown data built from unlicensed sources, such as a screener refreshed
    with yfinance: both only for an account allowed to see such data (see
    :func:`app.api.auth.sees_unlicensed_data`).

    ``user_id`` names the account the request is for, so price history fetched on its keys
    can be kept for that account alone (see :mod:`app.db.price_store`); ``None`` outside
    a signed-in request. ``store_history`` is false for bulk work, such as the nightly
    refresh, whose thousands of histories are not worth keeping.
    """

    anthropic: SecretStr | None = None
    fmp: SecretStr | None = None
    alpha_vantage: SecretStr | None = None
    finnhub: SecretStr | None = None
    tiingo: SecretStr | None = None
    eodhd: SecretStr | None = None
    yfinance: bool = False
    unlicensed: bool = False
    restricted_sources: bool = False
    """Whether the request may use data sources not cleared for a paid product even while
    the server does not: the admin's own, with their switch on."""
    user_id: str | None = None
    store_history: bool = True


def _key(value: str | None) -> SecretStr | None:
    """A sent key, or ``None`` for a missing, blank or implausible one."""
    value = (value or "").strip()
    if not MIN_KEY_LENGTH <= len(value) <= MAX_KEY_LENGTH or not value.isprintable():
        return None
    return SecretStr(value)


def provider_keys_from(
    headers: Mapping[str, str],
    yfinance: bool = False,
    unlicensed: bool = False,
    user_id: str | None = None,
    restricted_sources: bool = False,
) -> ProviderKeys:
    """The provider keys in a request's ``headers``, for ``user_id``'s request."""
    found: dict[str, SecretStr | None] = {
        name: _key(headers.get(header)) for name, header in PROVIDER_HEADERS.items()
    }
    return ProviderKeys(
        anthropic=found["anthropic"],
        fmp=found["fmp"],
        alpha_vantage=found["alpha_vantage"],
        finnhub=found["finnhub"],
        tiingo=found["tiingo"],
        eodhd=found["eodhd"],
        yfinance=yfinance,
        unlicensed=unlicensed,
        user_id=user_id,
        restricted_sources=restricted_sources,
    )


_current: ContextVar[ProviderKeys] = ContextVar("provider_keys", default=ProviderKeys())


def current_provider_keys() -> ProviderKeys:
    """The keys of the request being served; none outside a request."""
    return _current.get()


def use_provider_keys(keys: ProviderKeys) -> None:
    """Make ``keys`` the current request's provider keys."""
    _current.set(keys)
