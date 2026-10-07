"""Application configuration loaded from environment variables and ``.env``."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed runtime settings for PyQxel.

    Values are read from the process environment first, then from ``.env`` at the
    project root. Secrets are wrapped in :class:`SecretStr` so they are not leaked
    through ``repr`` or logging.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "PyQxel"
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost", "http://localhost:3000"]

    anthropic_api_key: SecretStr | None = Field(
        default=None,
        description="The server owner's Anthropic key. Never used to serve requests: each "
        "user's analyses run on the key stored in their account. Only the test suite and "
        "``python -m app.jobs.users create --import-env-keys`` read it.",
    )
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    anthropic_timeout_seconds: float = 180.0
    financial_data_api_key: SecretStr | None = Field(
        default=None,
        description="The server owner's Financial Modeling Prep key. Like "
        "``anthropic_api_key``, never used to serve requests.",
    )
    credentials_encryption_key: SecretStr | None = Field(
        default=None,
        description="Fernet key that encrypts the provider keys users store; generate one "
        "with ``python -m app.jobs.users generate-encryption-key``. Users cannot store "
        "keys without it.",
    )
    yfinance_fallback: bool = Field(
        default=False,
        description="For development: use yfinance when a user has no FMP key, or FMP "
        "fails or their plan does not cover a request. Off in production, where licensed "
        "data is required.",
    )
    rate_limit_per_minute: int = Field(
        default=120,
        ge=1,
        description="Requests each user may make per minute, across all endpoints.",
    )
    sec_user_agent: str | None = None
    sec_section_max_chars: int = Field(default=60_000, ge=1_000)
    r_home: Path | None = None

    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data_cache' / 'pyqxel.db'}",
        description="SQLAlchemy async URL for stored backtests, e.g. "
        "postgresql+asyncpg://user:pass@host/pyqxel.",
    )
    redis_url: SecretStr | None = Field(
        default=None,
        description="Redis URL for caching market data; caching is off when unset.",
    )
    screener_min_market_cap: float = Field(
        default=50_000_000.0,
        ge=0,
        description="Smallest market cap, in US dollars, a stock needs to enter the screened "
        "universe.",
    )
    screener_min_dollar_volume: float = Field(
        default=1_000_000.0,
        ge=0,
        description="Smallest average daily dollar volume a stock needs to enter the universe.",
    )
    screener_min_price: float = Field(
        default=2.0,
        ge=0,
        description="Smallest share price a stock needs to enter the universe, leaving out "
        "penny stocks.",
    )
    screener_fundamentals_refresh_days: float = Field(
        default=7.0,
        gt=0,
        description="How often the universe's financials are rebuilt from the SEC's bulk "
        "archive; companies file quarterly, so weekly keeps them current.",
    )
    analysis_cache_ttl_seconds: float = Field(
        default=86_400.0,
        ge=0,
        description="How long a stored AI analysis is reused instead of paying for a new "
        "one; 0 always writes a new one.",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` instance, created on first use."""
    return Settings()
