"""MCP server for Hoard Hurt Help.

The MCP layer uses Google OAuth, resolves the signed-in user to the canonical
Mode A connection, and calls the shared play service in-process.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentAccessToken, Depends
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.dependencies import AccessToken
from key_value.aio.stores.memory import MemoryStore
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.deps import assert_connection_usable, require_agent_player
from app.engine.agent_play import (
    chat_transcript,
    get_next_turn as play_get_next_turn,
    opponent_history,
    poll_turn,
    standings,
    submit_action as play_submit_action,
    submit_talk as play_submit_talk,
    turn_detail,
)
from app.engine.connection_activity import mark_seen
from app.engine.mode_a_connection import mode_a_connection_for
from app.models.connection import Connection
from app.models.player import Player
from app.routes.auth import sync_google_user
from app.routes.spectator_api import public_state
from app.schemas.auth import GoogleUserInfo

logger = logging.getLogger(__name__)

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
    return GoogleProvider(
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
        client_storage=MemoryStore(),
    )


mcp_app = FastMCP(
    "hoardhurthelp",
    auth=_build_auth_provider(),
)


# How long get_next_turn asks the API to bounded-long-poll while waiting for a
# turn (Mode A: interactive play). Kept under typical MCP client request timeouts
# (commonly ~30s) and matched to the API's MCP_LONG_POLL_HOLD_SECONDS default.
_NEXT_TURN_HOLD_SECONDS = 25.0
_LAST_POLL: dict[int, float] = {}
_LAST_PULL: dict[tuple[int, str], float] = {}


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
    claims = token.claims or {}
    sub = claims.get("sub") or token.subject or token.client_id
    email = claims.get("email")
    if not isinstance(sub, str) or not sub.strip():
        raise RuntimeError("Google access token is missing the subject claim.")
    if not isinstance(email, str) or not email.strip():
        raise RuntimeError("Google access token is missing the email claim.")
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


async def _resolve_oauth_connection(
    db: AsyncSession,
    token: object,
) -> tuple[AccessToken, GoogleUserInfo, Connection]:
    access_token = _require_access_token(token)
    userinfo = _google_userinfo_from_token(access_token)
    user = await sync_google_user(db, userinfo)
    connection = await mode_a_connection_for(db, user)
    assert_connection_usable(connection)
    await mark_seen(db, connection, key_hash=connection.key_lookup)
    return access_token, userinfo, connection


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


@mcp_app.tool()
async def get_turn(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(get_session)),
) -> Any:
    """Poll for the current turn."""
    resolved_match_id = _resolve_match_id(match_id, game_id)
    _access_token, _userinfo, _connection, player = await _resolve_oauth_player(
        db,
        token,
        match_id=resolved_match_id,
    )
    return await poll_turn(
        db,
        match_id=resolved_match_id,
        player=player,
        rate_state=_LAST_POLL,
    )


@mcp_app.tool()
async def get_next_turn(
    *,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(get_session)),
) -> Any:
    """Get the most urgent pending turn across all of the user's games."""
    _access_token, _userinfo, connection = await _resolve_oauth_connection(db, token)
    return await play_get_next_turn(
        db,
        connection,
        hold_seconds=_NEXT_TURN_HOLD_SECONDS,
        interval_seconds=1.0,
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
    db: AsyncSession = cast(AsyncSession, Depends(get_session)),
) -> Any:
    """Submit the talk-phase message for the current turn."""
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
    db: AsyncSession = cast(AsyncSession, Depends(get_session)),
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
    db: AsyncSession = cast(AsyncSession, Depends(get_session)),
) -> Any:
    """Get the public state of any game."""
    _require_access_token(token)
    resolved_match_id = _resolve_match_id(match_id, game_id)
    return await public_state(match_id=resolved_match_id, db=db)


@mcp_app.tool()
async def get_opponent_history(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    opponent_id: str,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(get_session)),
) -> Any:
    """Pull the full move history between the user and one opponent."""
    resolved_match_id = _resolve_match_id(match_id, game_id)
    _access_token, _userinfo, _connection, player = await _resolve_oauth_player(
        db,
        token,
        match_id=resolved_match_id,
    )
    return await opponent_history(
        db,
        match_id=resolved_match_id,
        opponent_id=opponent_id,
        player=player,
        rate_state=_LAST_PULL,
    )


@mcp_app.tool()
async def get_chat(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    since: str | None = None,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(get_session)),
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


@mcp_app.tool()
async def get_turn_detail(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    round: int,
    turn: int,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(get_session)),
) -> Any:
    """Pull one resolved turn in full."""
    resolved_match_id = _resolve_match_id(match_id, game_id)
    _access_token, _userinfo, _connection, player = await _resolve_oauth_player(
        db,
        token,
        match_id=resolved_match_id,
    )
    return await turn_detail(
        db,
        match_id=resolved_match_id,
        round=round,
        turn=turn,
        player=player,
        rate_state=_LAST_PULL,
    )


@mcp_app.tool()
async def get_standings(
    *,
    match_id: str | None = None,
    game_id: str | None = None,
    token: AccessToken = cast(AccessToken, CurrentAccessToken()),
    db: AsyncSession = cast(AsyncSession, Depends(get_session)),
) -> Any:
    """Pull the full standings."""
    resolved_match_id = _resolve_match_id(match_id, game_id)
    _access_token, _userinfo, _connection, player = await _resolve_oauth_player(
        db,
        token,
        match_id=resolved_match_id,
    )
    return await standings(
        db,
        match_id=resolved_match_id,
        player=player,
        rate_state=_LAST_PULL,
    )


# The parent FastAPI app mounts this at the public root so the auth discovery
# URLs stay rooted at `/.well-known/...` while the MCP endpoint itself remains
# `/mcp`.
asgi_app = mcp_app.http_app(path="/mcp", transport="streamable-http")
