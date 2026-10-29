"""Schemas for accounts: API keys, provider keys sent with requests, and AI request limits."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints
from pydantic.json_schema import SkipJsonSchema


class ProviderCredential(BaseModel):
    """Whether the request carried a provider's key, and its last characters to recognize
    it by. PyQxel stores no provider keys."""

    configured: bool
    hint: str | None = Field(default=None, description="Last four characters, e.g. …a1b2.")


class AiLimits(BaseModel):
    """Caps on new AI requests (stock analyses, portfolio reviews and coding assistant
    answers), which are billed to the account's own Anthropic key.

    Reused reports do not count. ``null`` means no cap.
    """

    ai_requests_per_hour: int | None = Field(default=None, ge=0, le=10_000)
    ai_requests_per_day: int | None = Field(default=None, ge=0, le=100_000)


class AiUsage(BaseModel):
    """New AI requests made for the account recently."""

    last_hour: int = Field(ge=0)
    last_day: int = Field(ge=0)


class AdminSettingsUpdate(BaseModel):
    """Changes to the admin's settings; omitted fields are left as they are."""

    yfinance_fallback: bool | None = Field(
        default=None,
        description="Whether your own requests fall back to yfinance when no licensed "
        "provider answers.",
    )
    yfinance_fallback_for_everyone: bool | None = Field(
        default=None,
        description="Whether every account's requests, and the screener refresh, fall back "
        "to yfinance. yfinance is unlicensed, so this shows its data to other people.",
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
        "answers."
    )
    sec_filings: bool = Field(
        description="Whether the server reads financial statements and filings from SEC "
        "EDGAR, which needs no account key."
    )
    admin: SkipJsonSchema[dict[str, bool] | None] = Field(
        default=None, exclude_if=lambda settings: settings is None
    )
    """The admin's switches: ``yfinance_fallback``, ``yfinance_fallback_for_everyone``, and
    the read-only ``yfinance_fallback_from_server`` (``YFINANCE_FALLBACK``). Sent to the
    admin alone, and left out of the API schema, so other accounts cannot tell there are
    admin settings. A plain dict, since FastAPI would list a model's schema even for a
    field it skips."""


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
