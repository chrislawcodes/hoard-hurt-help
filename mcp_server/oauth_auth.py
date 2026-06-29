"""OAuth / JWT plumbing for the MCP server.

This module owns concern #1 of the MCP layer: building the Google OAuth proxy
that signs MCP clients in, the durable client/token store that proxy uses, the
unsigned-JWT decode helpers used to read Google's identity claims, and the
connect-at-sign-in hook that syncs the user the moment the token exchange
completes.

It does NOT resolve a token to an MCP connection (see
``connection_identity``) and does NOT define any MCP tool (see ``mcp_tools``).
``server`` assembles these pieces and re-exposes the public names so external
imports keep working.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Mapping
from typing import Any

from fastmcp.server.auth.providers.google import GoogleProvider
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.memory import MemoryStore
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.routes.auth import sync_google_user
from app.models.user import User
from app.schemas.auth import GoogleUserInfo

logger = logging.getLogger(__name__)


def _build_client_storage() -> AsyncKeyValue:
    """Durable storage for the OAuth proxy's client + token records.

    The FastMCP access token is a *reference* token: on every authenticated call the
    server verifies the JWT signature and then looks up server-side state (the
    registered client record and the encrypted upstream Google token) keyed by the
    token's JTI. So this store — not just the signing key — must survive a restart, or
    every client has to redo the Google sign-in after each deploy.

    Prod (Postgres) uses a DB-backed store (Railway's disk is wiped on deploy, so a
    file store would not survive); dev/test (SQLite) uses in-memory, which is fine
    because we don't deploy those.
    """
    db_url = settings.database_url
    if db_url.startswith("postgresql"):
        # Imported lazily so a missing optional backend can never break /mcp in dev.
        from key_value.aio.stores.postgresql import PostgreSQLStore
        from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

        # PostgreSQLStore uses raw asyncpg and needs a plain postgresql:// URL;
        # app.config rewrites DATABASE_URL to the +asyncpg SQLAlchemy form.
        pg_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        store = PostgreSQLStore(url=pg_url, table_name="mcp_oauth_kv")
        # Encrypt the upstream Google tokens at rest. The provider only auto-encrypts
        # its *default* store; we pass an explicit store, so we wrap it ourselves with
        # a key derived from a stable secret (mirrors the provider's own behavior).
        secret = settings.mcp_jwt_signing_key.strip() or settings.google_client_secret.strip()
        return FernetEncryptionWrapper(
            store, source_material=secret, salt="hoardhurthelp-mcp-oauth-store"
        )
    return MemoryStore()


def _decode_unverified_jwt_payload(jwt_token: str) -> dict[str, Any]:
    """Decode a JWT's payload claims into a dict WITHOUT verifying its signature.

    Pure decode step: split the three segments, restore base64url padding, JSON
    decode, and confirm the payload is an object. Raises ``ValueError`` on a
    malformed JWT and propagates the underlying decode/JSON errors otherwise.
    Callers choose their own error policy around this (raise vs fail-open).
    """
    parts = jwt_token.split(".")
    if len(parts) != 3:
        raise ValueError("token is not a well-formed JWT")
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)  # restore base64url padding
    claims = json.loads(base64.urlsafe_b64decode(payload))
    if not isinstance(claims, dict):
        raise ValueError("JWT payload is not a JSON object")
    return claims


def _decode_jwt_claims(jwt_token: str) -> dict[str, Any]:
    """Read a JWT's payload claims WITHOUT verifying its signature.

    Only used on the Google id_token, which arrives straight from Google's token
    endpoint over a server-to-server TLS call (never from the client), so the
    signature is already trusted. We read identity claims (sub, email) only.

    Raises on a malformed id_token — the caller depends on this failing loud.
    """
    return _decode_unverified_jwt_payload(jwt_token)


def _userinfo_from_claims(
    claims: Mapping[str, Any], *, subject: str | None = None
) -> GoogleUserInfo:
    """Build a GoogleUserInfo from a claims mapping (id_token or access token)."""
    sub = claims.get("sub") or subject
    email = claims.get("email")
    if not isinstance(sub, str) or not sub.strip():
        raise RuntimeError("Google identity is missing the subject claim.")
    if not isinstance(email, str) or not email.strip():
        raise RuntimeError("Google identity is missing the email claim.")
    email_verified = claims.get("email_verified", True)
    if isinstance(email_verified, str):
        email_verified = email_verified.strip().lower() == "true"
    return GoogleUserInfo(
        sub=sub,
        email=email,
        name=claims.get("name"),
        given_name=claims.get("given_name"),
        family_name=claims.get("family_name"),
        email_verified=bool(email_verified),
    )


async def _bootstrap_signin_connection_from_idp(idp_tokens: Mapping[str, Any]) -> None:
    """Sync the signed-in user the moment the OAuth token exchange completes.

    Runs inside the token exchange (see _ConnectAtSignInGoogleProvider) — the one
    server-side point that fires exactly once per sign-in AND already knows who
    the user is. We do NOT create a connection here: each provider gets its own
    MCP connection, and at sign-in we don't yet know which AI client (provider)
    is connecting. The connection is created a moment later at the MCP initialize
    handshake, where ``clientInfo`` names the provider. Identity comes from the
    Google id_token in the token response.
    """
    async with SessionLocal() as db:
        if await _sync_signin_user(db, idp_tokens) is not None:
            await db.commit()


async def _sync_signin_user(
    db: AsyncSession, idp_tokens: Mapping[str, Any]
) -> User | None:
    """Resolve the Google id_token to the signed-in user (no commit).

    Returns None when the token response carries no id_token (e.g. a refresh
    exchange), in which case there is nothing to identify the user with here.
    """
    id_token = idp_tokens.get("id_token")
    if not isinstance(id_token, str) or not id_token.strip():
        return None
    userinfo = _userinfo_from_claims(_decode_jwt_claims(id_token))
    return await sync_google_user(db, userinfo)


class _ConnectAtSignInGoogleProvider(GoogleProvider):
    """GoogleProvider that records the MCP connection as soon as sign-in
    finishes, so the connections page does not wait for the first MCP request.

    ``_extract_upstream_claims`` is FastMCP's documented override point for
    inspecting upstream identity during the token exchange; we hang the
    connection bootstrap off it without changing what gets embedded in the JWT.
    """

    async def _extract_upstream_claims(
        self, idp_tokens: dict[str, Any]
    ) -> dict[str, Any] | None:
        claims = await super()._extract_upstream_claims(idp_tokens)
        try:
            await _bootstrap_signin_connection_from_idp(idp_tokens)
        except Exception:
            # fail-open: advisory only — sign-in must not fail if the connection
            # bootstrap does; the session/tool paths still create it later.
            logger.warning(
                "connect-at-sign-in bootstrap failed; the connection will be "
                "created on the client's first MCP request instead",
                exc_info=True,
            )
        return claims


# How long the FastMCP-issued login (bearer) token stays valid. The default ties
# it to Google's 1-hour access-token life, which forces a FULL re-login every hour
# and after every deploy for clients that don't silently refresh (e.g. Claude
# Code). We issue our own long-lived reference token instead: FastMCP still
# re-validates and transparently refreshes the upstream Google token on every
# request (a revoked/expired Google session still fails), so this only stops the
# needless client-facing re-auth churn. Works because access_type=offline gets us
# a Google refresh token, so the lifetime isn't capped at the upstream expiry.
_MCP_ACCESS_TOKEN_TTL_SECONDS = 90 * 24 * 60 * 60  # 90 days


def _build_auth_provider() -> GoogleProvider:
    """Create the OAuth proxy used for MCP client sign-in.

    In local dev and tests we keep the app importable even when Google creds are
    absent by using placeholder values. The startup config check in app.main.py
    still fails loud in real deployments.
    """
    client_id = settings.google_client_id.strip() or "dev-google-client-id"
    client_secret = settings.google_client_secret.strip() or "dev-google-client-secret"
    if not settings.google_client_id.strip() or not settings.google_client_secret.strip():
        logger.warning(
            "MCP OAuth is using placeholder Google credentials; sign-in will not work "
            "until GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are configured."
        )
    # A stable signing key keeps issued JWTs valid across restarts. When unset it is
    # derived deterministically from the (stable) client secret, so this is belt-and-
    # suspenders unless the client secret is ever rotated.
    signing_key = settings.mcp_jwt_signing_key.strip() or None
    return _ConnectAtSignInGoogleProvider(
        client_id=client_id,
        client_secret=client_secret,
        base_url=settings.base_url.rstrip("/"),
        resource_base_url=settings.base_url.rstrip("/"),
        issuer_url=settings.base_url.rstrip("/"),
        required_scopes=["openid", "email", "profile"],
        extra_authorize_params={
            "access_type": "offline",
            "prompt": "consent",
        },
        client_storage=_build_client_storage(),
        jwt_signing_key=signing_key,
        # Issue a long-lived login token so clients aren't kicked out every hour /
        # after each deploy (see _MCP_ACCESS_TOKEN_TTL_SECONDS).
        fastmcp_access_token_expiry_seconds=_MCP_ACCESS_TOKEN_TTL_SECONDS,
        # Skip FastMCP's built-in Allow/Deny consent interstitial. It confuses
        # non-expert users (it shows a raw 127.0.0.1 callback) and leaves dead
        # tabs behind. Google's own sign-in/consent still gates every login, and
        # this is a first-party CLI flow (PKCE + loopback redirect), so the
        # "confused deputy" risk the screen guards against is low for us.
        require_authorization_consent=False,
    )
