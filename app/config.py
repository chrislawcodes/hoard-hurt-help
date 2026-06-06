"""Settings loaded from environment variables.

Single source of truth for runtime config. Other modules import
`settings` from here; nothing else should touch `os.environ`.
"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Public-facing base URL of this deployment. Drives setup commands shown
    # to players, the OAuth redirect, the MCP server URL, etc.
    base_url: str = Field(default="http://localhost:8000")

    # Database connection. SQLite for dev, Postgres on Railway.
    database_url: str = Field(default="sqlite+aiosqlite:///./hoardhurthelp.db")

    # Google OAuth client. Required for sign-in.
    google_client_id: str = Field(default="")
    google_client_secret: str = Field(default="")
    google_redirect_uri: str = Field(default="http://localhost:8000/auth/google/callback")

    # Signing key for session cookies. Generate with `secrets.token_hex(32)`.
    session_secret: str = Field(default="dev-only-do-not-use-in-prod-" + "x" * 40)

    # Mark the session cookie Secure (HTTPS-only). Set true in production behind
    # HTTPS; leave false for local http dev.
    cookie_secure: bool = Field(default=False)

    # Comma-separated list of emails with admin powers.
    admin_emails: str = Field(default="")

    @field_validator("database_url")
    @classmethod
    def _force_async_driver(cls, v: str) -> str:
        """Normalize a sync Postgres URL to the asyncpg driver.

        Railway's Postgres add-on hands out a sync URL (``postgres://`` or
        ``postgresql://``), but our engine uses ``create_async_engine`` and
        needs the asyncpg driver. Rewriting here lets a deploy paste Railway's
        ``${{Postgres.DATABASE_URL}}`` value verbatim. SQLite and an already
        async URL pass through untouched. Alembic re-strips the suffix for its
        own sync run in migrations/env.py.
        """
        if v.startswith("postgresql+asyncpg://"):
            return v
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v

    @property
    def admin_emails_set(self) -> set[str]:
        """Normalized lowercased set of admin emails."""
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()


settings = get_settings()

PROVIDER_MODELS: dict[str, list[str]] = {
    "claude": [
        "claude-haiku-4-5",
        "claude-sonnet-4-6",
        "claude-opus-4-8",
    ],
    "gemini": [
        "gemini-3.1-flash-lite",
        "gemini-3-flash-preview",
        "gemini-3.1-pro-preview",
    ],
    "openai": [
        "gpt-5.4-mini",
        "gpt-5.4",
        "gpt-5.5",
    ],
    "hermes": [],
    "openclaw": [],
}
