"""Connection / player identity resolution for the MCP server.

This module owns concern #2 of the MCP layer: turning a verified OAuth access
token into the caller's canonical MCP ``Connection`` (creating it on first
sight), figuring out which AI provider a live client speaks for, reading the
per-client DCR id off the raw bearer token, and resolving a connection to the
seated ``Player`` for a given match.

Why the dependency imports live here (and are read as module globals): the tests
monkeypatch these collaborators — ``sync_google_user``, ``mcp_connection_for``,
``assert_connection_usable``, ``mark_seen``, ``require_agent_player``,
``get_http_request`` — and the helper functions in this module read them from
*this* module's namespace, so patching them on ``connection_identity`` takes
effect. ``server`` re-exposes the public names; the MCP tools call into this
module via the module object so intra-module patches are observed.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from fastmcp.server.dependencies import AccessToken, get_context, get_http_request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.deps import assert_connection_usable, require_agent_player
from app.engine.connection_activity import mark_seen
from app.engine.mcp_client_identity import provider_from_client_name
from app.engine.mcp_connection import mcp_connection_for
from app.models.connection import Connection, ConnectionProvider
from app.models.player import Player
from app.routes.auth import sync_google_user
from app.schemas.auth import GoogleUserInfo

from mcp_server.oauth_auth import _decode_unverified_jwt_payload, _userinfo_from_claims

logger = logging.getLogger(__name__)


def _require_access_token(token: object) -> AccessToken:
    if not isinstance(token, AccessToken):
        raise RuntimeError("MCP tool auth requires a verified access token.")
    return token


def _google_userinfo_from_token(token: AccessToken) -> GoogleUserInfo:
    return _userinfo_from_claims(
        token.claims or {}, subject=token.subject or token.client_id
    )


def _client_provider_from_context() -> ConnectionProvider | None:
    """Which AI provider does the live MCP client speak for? Best-effort.

    Reads the client's self-reported ``clientInfo.name`` from the active MCP
    session and maps it to a provider (one MCP client == one provider). Used on
    tool calls, where the session's ``client_params`` is already populated.

    fail-open: advisory only — any problem returns ``None`` and the caller
    enables no provider rather than guessing one.
    """
    try:
        ctx = get_context()
        params = getattr(ctx.session, "client_params", None)
        client_info = getattr(params, "clientInfo", None)
        name = getattr(client_info, "name", None)
    except Exception:
        return None
    return provider_from_client_name(name)


def _client_provider_from_initialize(message: object) -> ConnectionProvider | None:
    """Provider for the client in an ``initialize`` handshake message.

    The initialize request carries ``clientInfo`` directly, so this is reliable
    even before the session's ``client_params`` has been populated. fail-open:
    advisory only — unrecognized/missing names yield ``None``.
    """
    params = getattr(message, "params", message)
    client_info = getattr(params, "clientInfo", None)
    name = getattr(client_info, "name", None)
    return provider_from_client_name(name)


def _dcr_client_id_from_request() -> str | None:
    """The per-client OAuth registration id, read from the raw bearer token.

    Why not ``token.client_id``? After FastMCP validates a request it hands us an
    ``AccessToken`` whose ``client_id`` is the Google *subject* — the user's
    account id, identical for every AI client the same person signs in with (see
    fastmcp ``providers/google.py``: ``AccessToken(client_id=sub)``). That cannot
    tell one user's Codex from their Gemini, so it must not be used to route
    between a user's connections.

    The genuinely per-client id is the Dynamic Client Registration ``client_id``
    (a UUID minted once per ``mcp add``). FastMCP embeds it in the reference JWT it
    issues to the client but drops it during token validation, so we read it back
    from the raw ``Authorization: Bearer`` header. The client sends the same JWT
    on every request, so this is a stable per-client key. The bearer is already
    verified by FastMCP before any of our code runs (the request reached an
    authenticated tool/middleware), so we only decode the JWT payload to read the
    routing claim — we do not re-verify the signature here.

    fail-open: advisory routing only — any problem returns ``None`` and the caller
    falls back to the provider / single-connection lookup.
    """
    try:
        request = get_http_request()
        header = request.headers.get("authorization") or ""
        scheme, _, raw = header.partition(" ")
        if scheme.lower() != "bearer" or not raw:
            return None
        claims = _decode_unverified_jwt_payload(raw)
        client_id = claims.get("client_id")
        return client_id if isinstance(client_id, str) and client_id else None
    except Exception:
        # fail-open: advisory routing only — fall back to provider/single lookup.
        logger.debug("could not read DCR client_id from bearer token", exc_info=True)
        return None


async def _connection_from_token(
    db: AsyncSession,
    token: object,
    *,
    provider: ConnectionProvider | None,
    oauth_client_id: str | None = None,
) -> tuple[AccessToken, GoogleUserInfo, Connection]:
    """Resolve a verified OAuth token to the caller's MCP connection.

    Creates the connection on first sight and reuses it thereafter. ``provider``
    is the single provider the connecting MCP client speaks for — each provider
    gets its own connection (one client == one provider). ``oauth_client_id``
    is the OAuth Dynamic Client Registration client_id from the token; it is the
    primary lookup key in stateless-HTTP mode where session memory is unavailable.
    Does NOT record the call — see ``mark_seen`` for the heartbeat /
    usage-count side of a request.
    """
    access_token = _require_access_token(token)
    userinfo = _google_userinfo_from_token(access_token)
    user = await sync_google_user(db, userinfo)
    connection = await mcp_connection_for(db, user, provider=provider, oauth_client_id=oauth_client_id)
    if connection is None:
        # No provider to key on and no single existing connection to fall back to —
        # we genuinely can't tell which AI client this is. Fail loud rather than
        # guess a connection that might belong to the wrong provider.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "UNKNOWN_MCP_CLIENT",
                    "message": (
                        "Couldn't tell which AI client connected. Reconnect from a "
                        "supported client (Claude Code, Codex, or Gemini CLI)."
                    ),
                    "details": {},
                }
            },
        )
    assert_connection_usable(connection)
    return access_token, userinfo, connection


async def _resolve_oauth_connection(
    db: AsyncSession,
    token: object,
) -> tuple[AccessToken, GoogleUserInfo, Connection]:
    # The per-client identity is the DCR client_id read from the raw bearer JWT.
    # In stateless-HTTP mode the session's clientInfo is wiped between requests, so
    # the provider can't be read on a tool call; and token.client_id is the Google
    # subject (one value per user, shared by all their clients), so it can't route
    # between a user's Codex and Gemini. The DCR id is the only stable per-client key.
    oauth_client_id = _dcr_client_id_from_request()
    access_token, userinfo, connection = await _connection_from_token(
        db, token, provider=None, oauth_client_id=oauth_client_id
    )
    await mark_seen(db, connection, key_hash=connection.key_lookup)
    return access_token, userinfo, connection


async def _bootstrap_signin_connection(
    token: object, provider: ConnectionProvider | None
) -> None:
    """Create/resume the caller's MCP connection when their session starts.

    Runs once per MCP session, on the ``initialize`` handshake, so the
    /me/connections page flips to "connected" as soon as the client signs in —
    instead of only after the first tool call. ``provider`` is the single
    provider the connecting client speaks for (from its ``clientInfo``); it is
    enabled on the connection so the page reflects which client really connected.
    The handshake is not a paid model inference, so this deliberately skips
    ``mark_seen`` (which bumps the ``api_call_count`` cost counter) and only
    writes the connection's existence and connected timestamps via
    ``mcp_connection_for``.

    fail-open: advisory only — if this fails the session still initializes, and
    the first tool call's ``_resolve_oauth_connection`` remains the authoritative
    place the connection is created and recorded.
    """
    oauth_client_id = _dcr_client_id_from_request()
    async with SessionLocal() as db:
        await _connection_from_token(db, token, provider=provider, oauth_client_id=oauth_client_id)
        await db.commit()


async def _resolve_oauth_player(
    db: AsyncSession,
    token: object,
    *,
    match_id: str,
    agent_id: int | None = None,
    agent_turn_token: str | None = None,
) -> tuple[AccessToken, GoogleUserInfo, Connection, Player]:
    access_token, userinfo, connection = await _resolve_oauth_connection(db, token)
    player = await require_agent_player(
        match_id=match_id,
        db=db,
        connection=connection,
        agent_id=agent_id,
        agent_turn_token=agent_turn_token,
    )
    return access_token, userinfo, connection, player
