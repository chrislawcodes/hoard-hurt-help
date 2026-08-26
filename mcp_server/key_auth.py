"""Connection-key authentication for /mcp — the non-OAuth way in.

This module owns concern #5 of the MCP layer: verifying an ``sk_conn_`` key
presented as the bearer credential, for the narrow set of connections whose
owner has opted in.

WHY THIS EXISTS. /mcp is Google-sign-in-only by design, and that stays the
default. Antigravity (Google's replacement for the retired Gemini CLI) cannot
complete the sign-in: measured against prod, it POSTs to /mcp with no
credential, correctly fetches our protected-resource metadata, and then retries
without ever calling the authorization server — so it never obtains a token
(google-antigravity/antigravity-cli#25, open since May 2026 and reproduced on
1.1.11). It *can* carry a static header. A key is the only credential it can
present.

WHY IT IS OPT-IN AND NARROW. A key used this way lives in the client's own
config file, which the model can read, and this game deliberately feeds
opponent-written chat into that same model — so a rival's message could talk an
agent into disclosing its own key. That risk is acceptable only where the owner
chose it, so:

- ``Connection.mcp_key_signin_enabled`` must be true; it defaults to false and is
  never set implicitly.
- Rejection is total: a key for a connection that has not opted in is refused
  exactly like an unknown key, so this cannot widen the door for anyone else.
- A rotated-out key (``prev_key_lookup``) is NOT accepted here, unlike the plain
  HTTP agent API. Reissuing a key is how an owner revokes a leaked one, and that
  has to take effect on /mcp immediately.

The verified identity is passed downstream in the token's claims rather than
re-derived: ``connection_identity`` reads ``CONNECTION_ID_CLAIM`` and loads that
exact connection, so a key can never resolve to a different one.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

# FastMCP's AccessToken SUBCLASSES the mcp SDK's, and the downstream
# `_require_access_token` isinstance-checks the FastMCP one — returning the base
# class here would fail that check on every key-authenticated call.
from fastmcp.server.auth.auth import AccessToken
from sqlalchemy import select

from app.db import SessionLocal
from app.engine.tokens import (
    CONNECTION_KEY_PREFIX as _CONNECTION_KEY_PREFIX,
)
from app.engine.tokens import (
    bot_key_lookup,
    connection_key_log_hint,
    looks_like_connection_key,
)
from app.models.connection import Connection

logger = logging.getLogger(__name__)

# Every connection key is minted with this prefix (app.engine.tokens). It is what
# tells a key bearer apart from a FastMCP-issued JWT, so the two auth paths never
# have to guess at each other's credentials.
# Re-exported from app.engine.tokens, which mints the keys. Kept importable
# from here because callers already reach for it at this name.
CONNECTION_KEY_PREFIX = _CONNECTION_KEY_PREFIX

# Claim carrying the connection this key authenticated as. Namespaced so it can
# never collide with a Google/OIDC claim on the OAuth path.
CONNECTION_ID_CLAIM = "hhh_connection_id"

__all__ = [*globals().get("__all__", []), "looks_like_connection_key"]


async def verify_connection_key(
    raw_key: str, *, scopes: Sequence[str]
) -> AccessToken | None:
    """Resolve an ``sk_conn_`` bearer to an AccessToken, or None to reject.

    Returns None for every failure — unknown key, rotated-out key, deleted
    connection, or key sign-in not enabled — so a caller learns only "rejected",
    never which of those it was.

    Deliberately stops there. The remaining lifecycle rules (paused connection,
    disabled account) belong to ``assert_connection_usable``, which every
    resolved connection passes through downstream; re-checking them here would
    duplicate that rule and answer a paused connection with a bare 401 instead of
    the "resume it to play" 403 the OAuth path already gives.

    ``scopes`` is the caller's own ``required_scopes`` rather than a list built
    here. FastMCP enforces those scopes on every authenticated call and the
    Google provider rewrites the short names into full URIs
    (``email`` -> ``https://www.googleapis.com/auth/userinfo.email``), so a
    hardcoded copy silently drifts and every key call 403s on insufficient_scope.
    """
    key_hash = bot_key_lookup(raw_key)
    async with SessionLocal() as db:
        connection = (
            await db.execute(
                select(Connection).where(
                    Connection.key_lookup == key_hash,
                    Connection.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    if connection is None:
        # Redacted through the one shared helper (`connection_key_log_hint`), which
        # both this and the HTTP agent API now use — see its docstring for why a
        # leading slice is the wrong shape here.
        logger.warning(
            "mcp key auth failed: no live connection for key ending %s",
            connection_key_log_hint(raw_key),
        )
        return None
    if not connection.mcp_key_signin_enabled:
        logger.warning(
            "mcp key auth failed: connection %s has not enabled key sign-in",
            connection.id,
        )
        return None

    return AccessToken(
        token=raw_key,
        client_id=f"connection-key:{connection.id}",
        scopes=list(scopes),
        # No expires_at: the key is the credential and stays valid until the owner
        # reissues it, which is exactly how the plain HTTP agent API behaves.
        expires_at=None,
        subject=str(connection.user_id),
        claims={CONNECTION_ID_CLAIM: connection.id},
    )
