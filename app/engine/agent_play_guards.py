"""Leaf primitives shared across the agent-play service.

Small, dependency-light helpers: the error builder, seat-name lookups, the
pull rate-limit check, agent-turn-token binding validators, and a match lookup.
This is the bottom layer — it must not import from the other ``agent_play_*``
modules.
"""

from __future__ import annotations

import time
from typing import Sequence

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import _parse_agent_turn_token
from app.models.match import Match
from app.models.player import Player

_PULL_MIN_INTERVAL = 1.0

PullRateState = dict[tuple[int, str], float]


def _err(code: str, message: str, http: int, details: dict | None = None) -> HTTPException:
    return HTTPException(
        status_code=http,
        detail={"error": {"code": code, "message": message, "details": details or {}}},
    )


def _seat_name_map(players: Sequence[Player]) -> dict[int, str]:
    return {player.agent_id: player.seat_name for player in players}


def _check_pull_rate_limit(rate_state: PullRateState, agent_id: int, bucket: str) -> None:
    now_t = time.monotonic()
    last = rate_state.get((agent_id, bucket), 0.0)
    if now_t - last < _PULL_MIN_INTERVAL:
        raise _err("RATE_LIMITED", "Pulling too fast.", status.HTTP_429_TOO_MANY_REQUESTS)
    rate_state[(agent_id, bucket)] = now_t


def _validate_agent_turn_binding(
    agent_turn_token: str, *, turn_token: str, match_id: str, agent_id: int
) -> None:
    token_turn_token, token_agent_id, token_match_id = _parse_agent_turn_token(
        agent_turn_token
    )
    if (
        token_turn_token != turn_token
        or token_agent_id != agent_id
        or token_match_id != match_id
    ):
        raise _err(
            "STALE_TURN_TOKEN",
            "agent_turn_token doesn't match this agent and turn.",
            status.HTTP_409_CONFLICT,
        )


def _validate_agent_match_binding(
    agent_turn_token: str, *, match_id: str, agent_id: int
) -> None:
    _, token_agent_id, token_match_id = _parse_agent_turn_token(agent_turn_token)
    if token_agent_id != agent_id or token_match_id != match_id:
        raise _err(
            "STALE_TURN_TOKEN",
            "agent_turn_token doesn't match this agent and match.",
            status.HTTP_409_CONFLICT,
        )


async def _game_for(match_id: str, db: AsyncSession) -> Match:
    return (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()
