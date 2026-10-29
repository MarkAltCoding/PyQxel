"""Schemas for accounts: API keys, stored provider credentials, and AI request limits."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, StringConstraints
from pydantic.json_schema import SkipJsonSchema

ProviderKey = Annotated[SecretStr, Field(min_length=8, max_length=512)]
"""A provider's API key, as entered; never echoed back."""


class ProviderCredential(BaseModel):
    """Whether a provider key is stored, and its last characters to recognize it by."""

    configured: bool
    hint: str | None = Field(default=None, description="Last four characters, e.g. …a1b2.")


class AiLimits(BaseModel):
    """Caps on new AI analyses, which are billed to the account's own Anthropic key.

    Reused reports do not count. ``null`` means no cap.
    """

    ai_requests_per_hour: int | None = Field(default=None, ge=0, le=10_000)
    ai_requests_per_day: int | None = Field(default=None, ge=0, le=100_000)


class AiUsage(BaseModel):
    """New AI analyses written for the account recently."""

    last_hour: int = Field(ge=0)
    last_day: int = Field(ge=0)


class AdminSettings(BaseModel):
    """Settings only the admin account (``ADMIN_EMAIL``) has."""

    yfinance_fallback: bool = Field(
        description="Whether your own requests fall back to yfinance when no licensed "
        "provider answers. yfinance is unlicensed, so it is never used for other accounts."
    )


class Account(BaseModel):
    """The authenticated user's account."""

    id: UUID
    email: str
    created_at: datetime
    anthropic: ProviderCredential
    fmp: ProviderCredential
    alpha_vantage: ProviderCredential
    finnhub: ProviderCredential
    limits: AiLimits
    usage: AiUsage
    rate_limit_per_minute: int = Field(description="Requests allowed per minute, server-wide.")
    market_data_fallback: bool = Field(
        description="Whether your requests fall back to yfinance when no licensed provider "
        "answers, as in development."
    )
    sec_filings: bool = Field(
        description="Whether the server reads financial statements and filings from SEC "
        "EDGAR, which needs no account key."
    )
    admin: SkipJsonSchema[dict[str, bool] | None] = Field(
        default=None, exclude_if=lambda settings: settings is None
    )
    """The admin's :class:`AdminSettings`. Sent to the admin alone, and left out of the API
    schema, so other accounts cannot tell there are admin settings. A plain dict, since
    FastAPI would list a model's schema even for a field it skips."""


class CredentialsUpdate(BaseModel):
    """Provider keys to store. Omitted fields are left as they are; ``null`` removes a key.

    Keys are encrypted at rest and only ever used for this account's own requests.
    """

    anthropic_api_key: ProviderKey | None = Field(
        default=None, description="Anthropic API key, used for this account's analyses."
    )
    fmp_api_key: ProviderKey | None = Field(
        default=None, description="Financial Modeling Prep key, used for its market data."
    )
    alpha_vantage_api_key: ProviderKey | None = Field(
        default=None,
        description="Alpha Vantage key, used for market data and financial statements the "
        "other sources leave out.",
    )
    finnhub_api_key: ProviderKey | None = Field(
        default=None, description="Finnhub key, used for company news."
    )


class ApiKeyCreate(BaseModel):
    """A new PyQxel API key for the account."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ApiKeyInfo(BaseModel):
    """A PyQxel API key, without the key itself."""

    id: UUID
    name: str
    prefix: str = Field(description="The key's first characters, to tell keys apart.")
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    expires_at: datetime | None = Field(
        default=None,
        description="When the key stops working: set for keys issued by signing in, null "
        "for keys created for scripts.",
    )


class NewApiKey(ApiKeyInfo):
    """A newly created API key. ``key`` is shown this once and cannot be retrieved later."""

    key: str
