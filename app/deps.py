"""FastAPI dependencies shared across routes."""

import logging
from typing import Annotated
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import Depends, Header, HTTPException, Path, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.auth.session import get_user_from_session
from app.db import get_session
from app.engine.connection_activity import mark_seen
from app.engine.match_id_rewrite import match_id_candidates
from app.engine.tokens import bot_key_lookup
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.connection import Connection, ConnectionStatus
from app.models.connection_setup import ConnectionSetup
from app.models.player import Player
from app.models.user import User

logger = logging.getLogger(__name__)

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(request: Request, db: DbSession) -> User | None:
    """Return the signed-in User or None (does not raise)."""
    return await get_user_from_session(request, db)


async def require_user(request: Request, db: DbSession) -> User:
    user = await get_user_from_session(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "NOT_SIGNED_IN",
                    "message": "Sign in with Google to continue.",
                    "details": {},
                }
            },
        )
    return user


async def require_user_with_handle(request: Request, db: DbSession) -> User:
    """Like ``require_user``, but bounce a handle-less agent owner to pick one.

    A handle is required to own an agent. New users meet this when they first
    head to the bots panel to create an agent; existing agent owners meet it at
    their next visit. Rather than fail, redirect to the handle form and bring
    them back to where they were headed via ``next``.
    """
    user = await require_user(request, db)
    if user.handle is None:
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": f"/me/handle?next={quote(target, safe='')}"},
        )
    return user


async def require_platform_admin(request: Request, db: DbSession) -> User:
    """Require the user to be in PLATFORM_ADMIN_EMAILS (or legacy ADMIN_EMAILS)."""
    user = await require_user(request, db)
    if user.email.lower() not in settings.platform_admin_emails_set:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "NOT_PLATFORM_ADMIN",
                    "message": "Platform admin access required.",
                    "details": {},
                }
            },
        )
    return user


async def require_game_admin(
    game: Annotated[str, Path()],
    request: Request,
    db: DbSession,
) -> User:
    """Require the user to be a game admin for the {game} path parameter."""
    user = await require_user(request, db)
    if user.email.lower() not in settings.game_admin_emails_for(game):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "NOT_GAME_ADMIN",
                    "message": f"Game admin access required for {game}.",
                    "details": {},
                }
            },
        )
    return user


def _parse_agent_turn_token(agent_turn_token: str) -> tuple[str, int, str]:
    """Decode `turn_token:agent_id:match_id`."""
    try:
        turn_token, agent_id_text, token_match_id = agent_turn_token.rsplit(":", 2)
        agent_id = int(agent_id_text)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "INVALID_AGENT_TURN_TOKEN",
                    "message": "Invalid agent_turn_token.",
                    "details": {},
                }
            },
        ) from exc
    if not turn_token or not token_match_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "INVALID_AGENT_TURN_TOKEN",
                    "message": "Invalid agent_turn_token.",
                    "details": {},
                }
            },
        )
    return turn_token, agent_id, token_match_id


async def require_connection(
    db: DbSession,
    x_connection_key: Annotated[str | None, Header(alias="X-Connection-Key")] = None,
) -> Connection:
    """Validate `X-Connection-Key` as a stable connection key and return it."""
    if not x_connection_key:
        logger.warning("agent auth failed: missing X-Connection-Key header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "INVALID_KEY",
                    "message": "Missing X-Connection-Key header.",
                    "details": {},
                }
            },
        )
    if not x_connection_key.startswith("sk_conn_"):
        logger.warning("agent auth failed: bad key prefix %s", x_connection_key[:11])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "INVALID_KEY",
                    "message": "Invalid X-Connection-Key.",
                    "details": {},
                }
            },
        )

    key_hash = bot_key_lookup(x_connection_key)
    connection = (
        await db.execute(
            select(Connection).where(
                or_(
                    Connection.key_lookup == key_hash,
                    Connection.prev_key_lookup == key_hash,
                )
            )
        )
    ).scalar_one_or_none()
    if connection is None:
        setup = (
            await db.execute(
                select(ConnectionSetup).where(
                    ConnectionSetup.key_lookup == key_hash,
                    ConnectionSetup.completed_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if setup is None:
            logger.warning(
                "agent auth failed: no connection for key prefix %s", x_connection_key[:11]
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "INVALID_KEY",
                        "message": "Invalid X-Connection-Key.",
                        "details": {},
                    }
                },
            )
        connection = Connection(
            user_id=setup.user_id,
            nickname=setup.nickname,
            provider=setup.provider,
            key_lookup=setup.key_lookup,
            key_hint=setup.key_hint,
            status=ConnectionStatus.PENDING,
        )
        db.add(connection)
        await db.flush()
        setup.connection_id = connection.id
        setup.completed_at = datetime.now(timezone.utc)
    if connection.status == ConnectionStatus.PAUSED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "CONNECTION_PAUSED",
                    "message": "This connection is paused; resume it to play.",
                    "details": {},
                }
            },
        )

    await mark_seen(db, connection, key_hash=key_hash)
    return connection


async def require_agent_player(
    match_id: Annotated[str, Path()],
    db: DbSession,
    connection: Annotated[Connection, Depends(require_connection)],
    agent_id: Annotated[int | None, Query()] = None,
    agent_turn_token: Annotated[str | None, Query()] = None,
) -> Player:
    """Resolve the authenticated connection's active player for one match.

    Writes bind themselves to an exact agent+match via `agent_turn_token`
    (`turn_token:agent_id:match_id`). Reads may pass `X-Agent-Id` explicitly,
    or default to the sole connection-owned player in that match when there is
    exactly one.
    """
    if agent_turn_token is not None:
        _, token_agent_id, token_match_id = _parse_agent_turn_token(agent_turn_token)
        if token_match_id != match_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "STALE_TURN_TOKEN",
                        "message": "agent_turn_token doesn't match the match.",
                        "details": {},
                    }
                },
            )
        if agent_id is None:
            agent_id = token_agent_id
        elif agent_id != token_agent_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "STALE_TURN_TOKEN",
                        "message": "agent_turn_token doesn't match the selected agent.",
                        "details": {},
                    }
                },
            )

    candidate_match_ids = match_id_candidates(match_id)
    rows = (
        await db.execute(
            select(Player, Agent)
            .join(Agent, Agent.id == Player.agent_id)
            .where(
                Player.match_id.in_(candidate_match_ids),
                Player.left_at.is_(None),
                Agent.connection_id == connection.id,
                Agent.kind == AgentKind.AI,
                Agent.status == AgentStatus.ACTIVE,
                Agent.archived_at.is_(None),
            )
        )
    ).all()
    players = [player for player, _agent in rows]
    if agent_id is None:
        if len(players) == 1:
            return players[0]
        if not players:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "NOT_IN_GAME",
                        "message": "This connection has no player in that match.",
                        "details": {},
                    }
                },
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "AGENT_ID_REQUIRED",
                    "message": "Provide agent_id for this match.",
                    "details": {},
                }
            },
        )

    player = next((p for p in players if p.agent_id == agent_id), None)
    if player is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "NOT_IN_GAME",
                    "message": "This connection has no player in that match.",
                    "details": {},
                }
            },
        )
    return player
