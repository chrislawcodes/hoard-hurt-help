"""Settings loaded from environment variables.

Single source of truth for runtime config. Other modules import
`settings` from here; nothing else should touch `os.environ`.
"""

from functools import lru_cache

from pydantic import Field
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
    # to players, OAuth redirect, Custom GPT manifest, etc.
    base_url: str = Field(default="http://localhost:8000")

    # Database connection. SQLite for dev, Postgres on Railway.
    database_url: str = Field(default="sqlite+aiosqlite:///./hoardhurthelp.db")

    # Google OAuth client. Required for sign-in.
    google_client_id: str = Field(default="")
    google_client_secret: str = Field(default="")
    google_redirect_uri: str = Field(default="http://localhost:8000/auth/google/callback")

    # Signing key for session cookies. Generate with `secrets.token_hex(32)`.
    session_secret: str = Field(default="dev-only-do-not-use-in-prod-" + "x" * 40)

    # Comma-separated list of emails with admin powers.
    admin_emails: str = Field(default="")

    @property
    def admin_emails_set(self) -> set[str]:
        """Normalized lowercased set of admin emails."""
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()


settings = get_settings()
