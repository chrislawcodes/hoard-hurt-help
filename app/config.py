"""Settings loaded from environment variables.

Single source of truth for runtime config. Other modules import
`settings` from here; nothing else should touch `os.environ`.
"""

import logging
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger(__name__)


def _parse_email_set(raw: str) -> set[str]:
    """Split a comma-separated email list into a normalized lowercased set."""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


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

    # How long the server may hold one poll open, and whether the idle lanes
    # (waiting for a scheduled start, or having no game at all) hold at all.
    # Both default to the shipped behaviour, so changing nothing changes nothing.
    #
    # They are settings because the real ceiling on a held request is the
    # production edge, and the only way to find it is to hold a real request
    # through it. Railway documents 300s; Railway's own support forum carries
    # reports of long-lived connections cut at 20-90s. Neither is trustworthy
    # for this service, and every hold-length decision depends on the answer.
    # Defaults are the shipped behaviour so the test suite (and any fresh deploy)
    # keeps today's timings. The intended production values are 90 / true:
    # 90s is the longest hold that passed against every testable client (Claude
    # Code, Codex, and Antigravity's `agy` each held a silent 90s request and
    # returned it cleanly) and sits well under the strictest client wall measured
    # (180s, Antigravity, which a user cannot raise).
    #
    # They stay settings rather than becoming the defaults until a 90s hold is
    # confirmed to survive Railway's edge, which has never been measured. Turning
    # them on is an environment change, so turning them back off needs no deploy.
    agent_long_poll_hold_seconds: int = Field(default=40, ge=1)
    agent_hold_idle_lanes: bool = Field(default=False)

    # Pooled Postgres connections held by this instance. SQLAlchemy's own
    # defaults (5 + 10) are a silent ceiling of 15 that the long-poll holds and
    # the per-match turn loops can reach together; these make the ceiling
    # explicit and tunable without a redeploy. Ignored on SQLite, whose pool
    # classes take neither argument.
    #
    # Sized against the WORST case, not steady state: Railway keeps the old
    # deployment serving alongside the new one for ~20s, so a deploy briefly
    # runs two full pools plus the pre-deploy migration. Railway's Postgres
    # image shipped `max_connections = 100` until 2026-06-16 and 500 after, and
    # this database predates that change — so assume 100 until `SHOW
    # max_connections;` says otherwise. 20 here means a deploy peaks near 45.
    # Raise these from the environment once the live number is confirmed.
    db_pool_size: int = Field(default=15, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)

    # Google OAuth client. Required for sign-in.
    google_client_id: str = Field(default="")
    google_client_secret: str = Field(default="")
    google_redirect_uri: str = Field(default="http://localhost:8000/auth/google/callback")

    # MCP OAuth bridge. The server signs its own client tokens and registers
    # one or more redirect URIs with Google.
    mcp_jwt_signing_key: str = Field(default="")
    mcp_redirect_uris: str = Field(default="")

    # Signing key for session cookies. Generate with `secrets.token_hex(32)`.
    session_secret: str = Field(default="dev-only-do-not-use-in-prod-" + "x" * 40)

    # Mark the session cookie Secure (HTTPS-only). Set true in production behind
    # HTTPS; leave false for local http dev.
    cookie_secure: bool = Field(default=False)

    # Dev-only login bypass (no Google OAuth), for local dev + automated UI
    # checks. OFF by default. Even when true it is ignored unless cookie_secure
    # is false, so it can never expose a sign-in bypass in production (prod runs
    # COOKIE_SECURE=true). See app/routes/dev_login.py.
    dev_login_enabled: bool = Field(default=False)

    # --- Admin ---
    # There are two roles: user, and platform admin. A platform admin runs the
    # game catalog, user handles, incidents, and every match. Listing an address
    # here grants that role at sign-in; `users.role` is the record of truth
    # afterwards.
    platform_admin_emails: str = Field(default="")

    # How many scheduled/registering/active matches one player may hold at once.
    # Platform admins are exempt.
    user_active_match_limit: int = Field(default=3)

    # Compatibility: legacy single-role admin list. Kept as the fallback for
    # PLATFORM_ADMIN_EMAILS while production still sets the old name. Removing
    # it is a deploy-ordering job, not a code cleanup: if ADMIN_EMAILS is still
    # the live variable, dropping this locks every admin out of the platform.
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
    def platform_admin_emails_set(self) -> set[str]:
        """Platform admins. Falls back to admin_emails during compat window."""
        raw = self.platform_admin_emails or self.admin_emails
        if not raw:
            return set()
        if self.platform_admin_emails == "" and self.admin_emails:
            _log.warning(
                "ADMIN_EMAILS fallback active — set PLATFORM_ADMIN_EMAILS to remove"
            )
        return _parse_email_set(raw)


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
