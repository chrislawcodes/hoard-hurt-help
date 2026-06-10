"""Settings loaded from environment variables.

Single source of truth for runtime config. Other modules import
`settings` from here; nothing else should touch `os.environ`.
"""

import logging
import os
from functools import lru_cache

from pydantic import Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger(__name__)


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

    # --- Admin role split ---
    # Platform admin: game catalog, user handles, incidents.
    platform_admin_emails: str = Field(default="")
    # Game admin: per-game match creation, strategy prompts, export.
    # Set GAME_ADMIN_EMAILS__HOARD_HURT_HELP=alice@example.com for each game.
    # (Populated at construction time via _collect_game_admin_emails below.)

    # Compatibility: legacy single-role admin list. Kept as fallback while
    # PLATFORM_ADMIN_EMAILS / GAME_ADMIN_EMAILS__* are being rolled out.
    # Remove this field once all prod env vars are updated.
    admin_emails: str = Field(default="")

    # Internal storage populated by _collect_game_admin_emails validator; not an env var.
    _game_admin_emails_raw: dict[str, str] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _collect_game_admin_emails(self) -> "Settings":
        """Scan os.environ for GAME_ADMIN_EMAILS__* and populate _game_admin_emails_raw."""
        prefix = "GAME_ADMIN_EMAILS__"
        result: dict[str, str] = {}
        for k, v in os.environ.items():
            if k.upper().startswith(prefix):
                result[k[len(prefix):].upper()] = v  # e.g. "HOARD_HURT_HELP" → value
        self._game_admin_emails_raw = result
        return self

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
        """Normalized lowercased set of admin emails (legacy; prefer platform_admin_emails_set)."""
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    @property
    def platform_admin_emails_set(self) -> set[str]:
        """Platform admins. Falls back to admin_emails during compat window."""
        raw = self.platform_admin_emails or self.admin_emails
        if not raw:
            return set()
        if self.platform_admin_emails == "" and self.admin_emails:
            _log.warning(
                "ADMIN_EMAILS fallback active — set PLATFORM_ADMIN_EMAILS to remove"
            )
        return {e.strip().lower() for e in raw.split(",") if e.strip()}

    def game_admin_emails_for(self, game: str) -> set[str]:
        """Return the game-admin email set for a slug like 'hoard-hurt-help'.

        Normalizes slug → uppercase with underscores to look up the env var suffix.
        Falls back to admin_emails during the compat window.
        """
        key = game.upper().replace("-", "_")
        raw = self._game_admin_emails_raw.get(key, "")
        if not raw and self.admin_emails:
            _log.warning(
                "ADMIN_EMAILS fallback active for game %s — set GAME_ADMIN_EMAILS__%s",
                game,
                key,
            )
            raw = self.admin_emails
        if not raw:
            return set()
        return {e.strip().lower() for e in raw.split(",") if e.strip()}

    @property
    def all_game_admin_emails_set(self) -> set[str]:
        """Union of all game-admin emails across every configured game."""
        result: set[str] = set()
        for raw in self._game_admin_emails_raw.values():
            result.update(e.strip().lower() for e in raw.split(",") if e.strip())
        return result


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


def _assert_unique_non_empty_provider_models(provider_models: dict[str, list[str]]) -> None:
    """Ensure the non-empty provider allowlists do not share a model name."""
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for provider, models in provider_models.items():
        if not models:
            continue
        for model in models:
            prior = seen.get(model)
            if prior is not None and prior != provider:
                duplicates.append(f"{model!r} in {prior} and {provider}")
            else:
                seen[model] = provider
    if duplicates:
        raise AssertionError(
            "Duplicate model names across non-empty provider allowlists: "
            + ", ".join(sorted(duplicates))
        )


_assert_unique_non_empty_provider_models(PROVIDER_MODELS)


def provider_for_model(model: str) -> str | None:
    """Reverse-map a model name to its provider via PROVIDER_MODELS.

    The single source of truth for model→provider (the assertion above keeps
    model names unique across the non-empty allowlists, so this is
    unambiguous). Returns None for a model in no allowlist — e.g. a freeform
    Hermes/OpenClaw model whose provider must come from elsewhere (the stored
    `agents.provider`), not from the model name.
    """
    for provider, models in PROVIDER_MODELS.items():
        if model in models:
            return provider
    return None
