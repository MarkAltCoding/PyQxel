"""Application configuration loaded from environment variables and ``.env``."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import make_url

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
        # Secrets pasted into a secret manager often end with a newline.
        str_strip_whitespace=True,
        # A blank line in .env, as copied from .env.example, means unset, not an empty key.
        env_ignore_empty=True,
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
    alpha_vantage_api_key: SecretStr | None = Field(
        default=None,
        description="The server owner's Alpha Vantage key. Like ``anthropic_api_key``, never "
        "used to serve requests.",
    )
    finnhub_api_key: SecretStr | None = Field(
        default=None,
        description="The server owner's Finnhub key. Like ``anthropic_api_key``, never used to "
        "serve requests.",
    )
    credentials_encryption_key: SecretStr | None = Field(
        default=None,
        description="Fernet key that encrypts the provider keys users store; generate one "
        "with ``python -m app.jobs.users generate-encryption-key``. Users cannot store "
        "keys without it.",
    )
    github_client_id: str | None = Field(
        default=None,
        description="Client ID of the GitHub OAuth app people sign in with; GitHub sign-in "
        "is offered when it and ``github_client_secret`` are set.",
    )
    github_client_secret: SecretStr | None = None
    google_client_id: str | None = Field(
        default=None,
        description="Client ID of the Google OAuth client people sign in with; Google "
        "sign-in is offered when it and ``google_client_secret`` are set.",
    )
    google_client_secret: SecretStr | None = None
    yfinance_fallback: bool = Field(
        default=False,
        description="For development or personal use: use yfinance when a user has no FMP "
        "key, or FMP fails or their plan does not cover a request, and yfinance and Nasdaq's "
        "public feed for the screener refresh when SCREENER_FMP_API_KEY is unset. These "
        "sources are unlicensed: keep it off once anyone else uses the deployment.",
    )
    admin_email: str | None = Field(
        default=None,
        description="Email of the account that sees admin settings, where it can turn the "
        "yfinance fallback on for its own requests only. Accounts are matched by the "
        "verified email they sign in with; no account is admin when unset.",
    )
    sign_in_key_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="Days an API key issued by signing in on the website stays valid; keys "
        "created for scripts do not expire.",
    )
    rate_limit_per_minute: int = Field(
        default=120,
        ge=1,
        description="Requests each user may make per minute, across all endpoints.",
    )
    sec_user_agent: str | None = None
    sec_section_max_chars: int = Field(default=60_000, ge=1_000)
    r_home: Path | None = None
    web_dist_dir: Path = Field(
        default=PROJECT_ROOT / "web" / "dist",
        description="The built web frontend (``npm run build`` in ``web/``), served at ``/`` "
        "when present.",
    )

    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data_cache' / 'pyqxel.db'}",
        description="SQLAlchemy async URL for stored backtests, e.g. "
        "postgresql+asyncpg://user:pass@host/pyqxel. Plain postgres:// URLs, as hosting "
        "platforms provide them, are converted.",
    )
    database_pgbouncer: bool = Field(
        default=False,
        description="Set when DATABASE_URL goes through PgBouncer in transaction mode, as "
        "pooled URLs from Neon and Supabase do: asyncpg's "
        "prepared statement cache then breaks, so it is turned off. Harmless otherwise.",
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
    screener_fmp_api_key: SecretStr | None = Field(
        default=None,
        description="Financial Modeling Prep key the screener refresh uses for prices, "
        "market caps, sectors and exchange rates. Every account sees the universe it builds, "
        "so it must be on a plan whose terms allow showing FMP data to your users, and cover "
        "the company screener. Without it, the refresh runs only with YFINANCE_FALLBACK on.",
    )
    screener_fmp_requests_per_minute: int = Field(
        default=300,
        ge=1,
        description="FMP requests the screener refresh makes per minute at most; set it to "
        "your plan's limit. A full price download takes one request per listed stock.",
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

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg(cls, value: str) -> str:
        """Point plain Postgres URLs at the asyncpg driver.

        asyncpg names libpq's ``sslmode`` ``ssl`` and rejects ``channel_binding``, which it
        negotiates by itself.
        """
        url = make_url(value)
        if url.drivername not in ("postgres", "postgresql", "postgresql+asyncpg"):
            return value
        query = dict(url.query)
        if "sslmode" in query:
            query["ssl"] = query.pop("sslmode")
        query.pop("channel_binding", None)
        return url.set(drivername="postgresql+asyncpg", query=query).render_as_string(
            hide_password=False
        )

    @field_validator("admin_email")
    @classmethod
    def _normalize_email(cls, value: str | None) -> str | None:
        """Store the address as accounts store theirs, so they compare equal; blank is unset."""
        return value.strip().lower() or None if value is not None else None


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` instance, created on first use."""
    return Settings()
