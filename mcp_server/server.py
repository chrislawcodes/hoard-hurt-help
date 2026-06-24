"""MCP server for Hoard Hurt Help.

The MCP layer uses Google OAuth, resolves the signed-in user to the canonical
MCP connection, and calls the shared play service in-process.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentAccessToken, Depends
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.dependencies import (
    AccessToken,
    get_access_token,
    get_context,
    get_http_request,
)
from fastapi import HTTPException, status
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.memory import MemoryStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.deps import assert_connection_usable, require_agent_player
from app.engine.agent_play import (
    agent_identity_for,
    chat_transcript,
    get_next_turn as play_get_next_turn,
    get_next_turns as play_get_next_turns,
    submit_action as play_submit_action,
    submit_talk as play_submit_talk,
)
from app.engine.connection_activity import mark_seen
from app.engine.mcp_client_identity import provider_from_client_name
from app.engine.mcp_connection import mcp_connection_for
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.connection import Connection, ConnectionProvider
from app.models.match import Match
from app.models.player import Player
from app.models.user import User
from app.routes.auth import sync_google_user
from app.routes.spectator_api import public_state
from app.schemas.auth import GoogleUserInfo
from app.games import get as get_game_module

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


def _decode_jwt_claims(jwt_token: str) -> dict[str, Any]:
    """Read a JWT's payload claims WITHOUT verifying its signature.

    Only used on the Google id_token, which arrives straight from Google's token
    endpoint over a server-to-server TLS call (never from the client), so the
    signature is already trusted. We read identity claims (sub, email) only.
    """
    parts = jwt_token.split(".")
    if len(parts) != 3:
        raise ValueError("id_token is not a well-formed JWT")
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)  # restore base64 padding
    claims = json.loads(base64.urlsafe_b64decode(payload))
    if not isinstance(claims, dict):
        raise ValueError("id_token payload is not a JSON object")
    return claims


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


mcp_app = FastMCP(
    "agentludum",
    auth=_build_auth_provider(),
)


# Caps the server's long-poll hold for the MCP path. The server decides the hold
# off game state, but MCP clients cut requests sooner than a plain HTTP curl
# (commonly ~30s), so we hold for less than that here.
_NEXT_TURN_HOLD_SECONDS = 25.0
_LAST_PULL: dict[tuple[int, str], float] = {}


@asynccontextmanager
async def _session_scope() -> AsyncIterator[AsyncSession]:
    """Per-call DB session for MCP tools, as an async context manager.

    FastMCP's DI (uncalled_for) resolves a ``Depends()`` value by entering it as
    an async context manager; unlike FastAPI it does NOT iterate a bare async
    generator. The app's ``get_session`` is a generator, so passing it to
    ``Depends`` would leave each tool with the raw generator object instead of a
    session (``'async_generator' object has no attribute 'execute'``). Wrapping
    in ``@asynccontextmanager`` gives uncalled_for something it can enter.
    """
    async with SessionLocal() as session:
        yield session


def _lean_payload_for_mcp(payload: dict[str, object]) -> dict[str, object]:
    """Drop the MCP-only duplicated static prompt text from a turn payload."""
    lean = dict(payload)
    lean.pop("strategy", None)
    static = lean.get("static")
    if isinstance(static, dict):
        lean_static = dict(static)
        lean_static.pop("rules", None)
        lean_static.pop("base_prompt", None)
        lean_static.pop("your_strategy", None)
        lean["static"] = lean_static
    turns = lean.get("turns")
    if isinstance(turns, list):
        lean["turns"] = [
            _lean_payload_for_mcp(turn) if isinstance(turn, dict) else turn
            for turn in turns
        ]
    return lean


def _resolve_match_id(match_id: str | None, game_id: str | None) -> str:
    if match_id and game_id and match_id != game_id:
        raise ValueError("match_id and game_id must match when both are provided")
    resolved = match_id or game_id
    if resolved is None:
        raise ValueError("match_id or game_id is required")
    return resolved


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
        segments = raw.split(".")
        if len(segments) != 3:
            return None  # not a JWT (e.g. an opaque token) — nothing to read
        body = segments[1]
        body += "=" * (-len(body) % 4)  # restore base64url padding
        claims = json.loads(base64.urlsafe_b64decode(body))
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


class SigninConnectionMiddleware(Middleware):
    """Bootstrap the MCP connection when a client initializes a session."""

    async def on_initialize(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        token = get_access_token()
        if token is not None:
            try:
                provider = _client_provider_from_initialize(context.message)
                await _bootstrap_signin_connection(token, provider)
            except Exception:
                # fail-open: advisory only — the first tool call still creates and
                # records the connection (see _bootstrap_signin_connection).
                logger.warning(
                    "sign-in connection bootstrap failed; the connection will be "
                    "created on the first tool call instead",
                    exc_info=True,
                )
        return await call_next(context)


mcp_app.add_middleware(SigninConnectionMiddleware())


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


def _mcp_how_to_play_block() -> str:
    return (
        "## How to play\n\n"
        "Keep calling get_next_turn(agent_id=...) in a loop. Never stop on your own — "
        "stop only when get_next_turn says should_stop=true.\n\n"
        '- status "your_turn": check current.phase\n'
        '  - If static.coach_note is present, treat it as a one-round instruction from your coach '
        '— follow it for this turn instead of (or on top of) your strategy.\n'
        '  - "talk": call submit_talk(match_id, turn_token, agent_turn_token, message, thinking). '
        'One message per turn. After it is accepted, call get_next_turn again right away — the '
        'server serves the "act" phase when it opens.\n'
        '  - "act": call submit_action(match_id, turn_token, agent_turn_token, action, target_id, message). '
        "After it is accepted, call get_next_turn again right away.\n"
        '- status "waiting": a turn is coming. Wait next_poll_after_seconds, then call again. '
        "If next_game_starts_in_seconds is present, tell me when the game starts.\n"
        '- status "no_game" with should_stop=false: no game yet. '
        "Wait next_poll_after_seconds, then call again.\n"
        '- status "no_game" with should_stop=true: stop and tell me — I\'ll start a game when ready.\n'
        "- Error (5xx / timeout): wait 30 seconds and retry, up to 3 times.\n\n"
        "The history in each turn is only the last couple of resolved turns — enough "
        "to react to. You hold the rest in this conversation. If you ever need the whole "
        "game (you joined one already in progress, or lost the thread), call "
        "get_game_state(match_id) once to catch up.\n\n"
        "Never run a shell `sleep`, and never wait for a turn's deadline or resolve time. "
        "get_next_turn does the waiting for you — it holds the request open until there is "
        "something to do (or it tells you to wait). Just call it again. "
        "Pause only when a reply gives you next_poll_after_seconds, and wait exactly that long "
        "(0 means now). The server sets the right wait for you.\n\n"
        "Call the tools. Do not answer in plain text or prose.\n\n"
        "Before you start the loop, restate in your own words what you will do for each status."
    )


def _format_instruction_sections(
    *,
    match: Match,
    your_agent_id: str,
    all_agent_ids: list[object],
    strategy_text: str,
) -> str:
    module = get_game_module(match.game)
    other_agent_ids = [agent_id for agent_id in all_agent_ids if agent_id != your_agent_id]
    lines = [
        "## The rules",
        "",
        module.semantic_rules_text(match.total_rounds, match.turns_per_round).rstrip(),
        "",
        "## You",
        "",
        f'You are "{your_agent_id}". You can target: {other_agent_ids}.',
    ]
    # A game with hidden per-player state contributes its own setup hint (plus a
    # trailing blank); games with none fall back to a single blank separator.
    lines.extend(module.mcp_setup_hint_lines() or [""])
    lines.extend(
        [
            "## Your strategy",
            "",
            strategy_text.rstrip(),
            "",
            _mcp_how_to_play_block(),
        ]
    )
    return "\n".join(lines).rstrip()


@mcp_app.tool()
async def get_next_turn(
    *,
    agent_id: int | None = None,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(_session_scope)),
) -> Any:
    """Get the most urgent pending turn across all of the user's games.

    Pass agent_id to get the next turn for ONE specific agent. Use this when you
    are running several agents at once: run one loop per agent in parallel, and
    give each loop its own agent_id so the agents' turns never wait on each other.
    Omit agent_id to play all agents from a single loop (most urgent first).
    """
    _access_token, _userinfo, connection = await _resolve_oauth_connection(db, token)
    # Pacing (the wait number + whether to long-poll) is decided server-side off
    # the caller's soonest game — see app.engine.agent_idle.pace_idle. We only cap
    # the hold here, since MCP clients cut requests sooner than a plain HTTP curl.
    payload = await play_get_next_turn(
        db, connection, agent_id=agent_id, max_hold_seconds=_NEXT_TURN_HOLD_SECONDS
    )
    return _lean_payload_for_mcp(payload)


@mcp_app.tool()
async def get_next_turns(
    *,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(_session_scope)),
) -> Any:
    """Return ALL of the user's currently-claimable turns at once, one per agent.

    Use this to discover how many agents you are running before fanning out: if it
    returns more than one turn, run one parallel loop per agent (call
    get_next_turn(agent_id=...) in each), so two agents on the same provider can
    both move inside the same turn window instead of waiting in line.
    """
    _access_token, _userinfo, connection = await _resolve_oauth_connection(db, token)
    payload = await play_get_next_turns(db, connection)
    return _lean_payload_for_mcp(payload)


@mcp_app.tool()
async def get_instructions(
    *,
    agent_id: int | None = None,
    match_id: str | None = None,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(_session_scope)),
) -> str:
    """Return the static play instructions for one agent, if one can be selected."""
    _access_token, _userinfo, connection = await _resolve_oauth_connection(db, token)
    match, your_agent_id, all_agent_ids, strategy_text = await agent_identity_for(
        db,
        connection,
        agent_id=agent_id,
        match_id=match_id,
    )
    if match is None or your_agent_id is None or strategy_text is None:
        active_agent_ids = sorted(
            (
                await db.execute(
                    select(Agent.id).where(
                        Agent.user_id == connection.user_id,
                        Agent.kind == AgentKind.AI,
                        Agent.status == AgentStatus.ACTIVE,
                        Agent.archived_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if agent_id is None and len(active_agent_ids) > 1:
            return (
                "You have multiple agents. Call get_instructions(agent_id=...) for each "
                f"one's strategy: {active_agent_ids}.\n\n"
                f"{_mcp_how_to_play_block()}"
            )
        return (
            "No active game yet. Start one, then call get_instructions again for that "
            "game's rules and your strategy."
        )
    return _format_instruction_sections(
        match=match,
        your_agent_id=your_agent_id,
        all_agent_ids=all_agent_ids,
        strategy_text=strategy_text,
    )


@mcp_app.tool()
async def submit_talk(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    message: str,
    thinking: str = "",
    turn_token: str,
    agent_turn_token: str,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(_session_scope)),
) -> Any:
    """Submit the talk-phase message for the current turn.

    If the talk window has already closed (the turn moved on to the act phase),
    this returns status "talk_window_closed" instead of an error — that is normal,
    not a failure. Just submit your action next; the turn_token is unchanged.
    """
    resolved_match_id = _resolve_match_id(match_id, game_id)
    _access_token, _userinfo, connection, player = await _resolve_oauth_player(
        db,
        token,
        match_id=resolved_match_id,
        agent_turn_token=agent_turn_token,
    )
    return await play_submit_talk(
        db,
        match_id=resolved_match_id,
        player=player,
        agent_turn_token=agent_turn_token,
        turn_token=turn_token,
        message=message,
        thinking=thinking,
        is_connector_fallback=False,
    )


@mcp_app.tool()
async def submit_action(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    action: str,
    target_id: str | None,
    message: str,
    turn_token: str,
    agent_turn_token: str,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(_session_scope)),
) -> Any:
    """Submit the act-phase move for the current turn."""
    resolved_match_id = _resolve_match_id(match_id, game_id)
    _access_token, _userinfo, connection, player = await _resolve_oauth_player(
        db,
        token,
        match_id=resolved_match_id,
        agent_turn_token=agent_turn_token,
    )
    return await play_submit_action(
        db,
        match_id=resolved_match_id,
        player=player,
        connection=connection,
        agent_turn_token=agent_turn_token,
        turn_token=turn_token,
        action=action,
        target_id=target_id,
        message=message,
        thinking="",
        is_connector_fallback=False,
    )


@mcp_app.tool()
async def get_game_state(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(_session_scope)),
) -> Any:
    """Get the public state of any game."""
    _require_access_token(token)
    resolved_match_id = _resolve_match_id(match_id, game_id)
    return await public_state(match_id=resolved_match_id, db=db)


@mcp_app.tool()
async def get_chat(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    since: str | None = None,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(_session_scope)),
) -> Any:
    """Pull the full public chat transcript."""
    resolved_match_id = _resolve_match_id(match_id, game_id)
    _access_token, _userinfo, _connection, player = await _resolve_oauth_player(
        db,
        token,
        match_id=resolved_match_id,
    )
    return await chat_transcript(
        db,
        match_id=resolved_match_id,
        player=player,
        rate_state=_LAST_PULL,
        since=since,
    )


# The parent FastAPI app mounts this at the public root so the auth discovery
# URLs stay rooted at `/.well-known/...` while the MCP endpoint itself remains
# `/mcp`.
#
# stateless_http=True: do NOT keep per-client session state in process memory.
# A stateful server hands each client an Mcp-Session-Id and tracks it in RAM, so
# every redeploy (Railway rolling-deploys on each merge) wipes that map and every
# connected client's next call fails with "Session not found" until the human
# manually reconnects — silently dropping active players mid-game. We don't use
# the features that statefulness buys (server-initiated SSE notifications): play
# is poll-based request/response, the long-poll lives inside a single request, and
# auth is per-call via a reference token. Going stateless makes each request
# self-contained, so a restart can't orphan a client.
asgi_app = mcp_app.http_app(
    path="/mcp", transport="streamable-http", stateless_http=True
)
