"""Schemas for accounts: API keys, stored provider credentials, and AI request limits."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, StringConstraints

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


class Account(BaseModel):
    """The authenticated user's account."""

    id: UUID
    email: str
    created_at: datetime
    anthropic: ProviderCredential
    fmp: ProviderCredential
    limits: AiLimits
    usage: AiUsage
    rate_limit_per_minute: int = Field(description="Requests allowed per minute, server-wide.")


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


class NewApiKey(ApiKeyInfo):
    """A newly created API key. ``key`` is shown this once and cannot be retrieved later."""

    key: str
