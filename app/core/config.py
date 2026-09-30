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

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    anthropic_timeout_seconds: float = 180.0
    financial_data_api_key: SecretStr | None = None
    sec_user_agent: str | None = None
    sec_section_max_chars: int = Field(default=60_000, ge=1_000)
    r_home: Path | None = None


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` instance, created on first use."""
    return Settings()
