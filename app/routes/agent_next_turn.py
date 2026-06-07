"""Connection-scoped next-turn endpoint.

The runner authenticates once per connection and receives the most urgent open
turn across all active AI agents on that connection. The payload names the
specific agent + version the turn belongs to so one runner can keep separate
sessions per agent without ever collapsing two agents in the same match.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.deps import DbSession, require_connection
from app.engine.next_turn import TurnCandidate, select_next_turn
from app.games import get as get_game_module
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission
from app.routes.agent_api import (
    _build_current_turn,
    _group_into_turns,
    _load_public_action_records,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])

_POLL_WHEN_WAITING = 5


def _as_aware(dt: datetime) -> datetime:
    """SQLite drops timezone info on read; normalize to UTC-aware for comparison."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _agent_turn_token(turn_token: str, agent_id: int, match_id: str) -> str:
    return f"{turn_token}:{agent_id}:{match_id}"


@router.get("/next-turn", response_model=None)
async def next_turn(
    connection: Annotated[Connection, Depends(require_connection)],
    db: DbSession,
) -> dict[str, object]:
    """Return the single most urgent open turn across this connection's agents."""
    agent_rows = (
        await db.execute(
            select(Agent, Player, Match, AgentVersion)
            .join(Player, Player.agent_id == Agent.id)
            .join(Match, Match.id == Player.match_id)
            .join(AgentVersion, AgentVersion.id == Agent.current_version_id, isouter=True)
            .where(
                Agent.connection_id == connection.id,
                Agent.kind == AgentKind.AI,
                Agent.status == AgentStatus.ACTIVE,
                Agent.archived_at.is_(None),
                Player.left_at.is_(None),
                Match.state == GameState.ACTIVE,
            )
        )
    ).all()
    if not agent_rows:
        return {"status": "waiting", "next_poll_after_seconds": _POLL_WHEN_WAITING}

    latest_turn_by_match: dict[str, Turn] = {}
    player_by_key: dict[tuple[int, str], Player] = {}
    agent_by_id: dict[int, Agent] = {}
    version_by_agent_id: dict[int, AgentVersion] = {}
    for agent, player, match, version in agent_rows:
        if version is None:
            # An AI agent with no current version cannot play (no model/strategy
            # to drive a turn). Skip it VISIBLY rather than letting an INNER JOIN
            # drop it silently — a silently-skipped agent is the freeze class.
            logger.warning(
                "next-turn: agent %s (connection %s) has no current version; skipping",
                agent.id,
                connection.id,
            )
            continue
        player_by_key[(agent.id, match.id)] = player
        agent_by_id[agent.id] = agent
        version_by_agent_id[agent.id] = version
        if match.id not in latest_turn_by_match:
            turn = (
                await db.execute(
                    select(Turn)
                    .where(Turn.match_id == match.id, Turn.resolved_at.is_(None))
                    .order_by(Turn.round.desc(), Turn.turn.desc(), Turn.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if turn is not None:
                latest_turn_by_match[match.id] = turn

    candidates: list[TurnCandidate] = []
    for agent_id, match_id in player_by_key:
        player = player_by_key[(agent_id, match_id)]
        turn = latest_turn_by_match.get(match_id)
        if turn is None:
            continue
        existing = (
            await db.execute(
                select(TurnSubmission.id).where(
                    TurnSubmission.turn_id == turn.id,
                    TurnSubmission.player_id == player.id,
                    TurnSubmission.was_defaulted.is_(False),
                )
            )
        ).first()
        if existing is not None:
            continue
        candidates.append(
            TurnCandidate(
                match_id=match_id,
                round=turn.round,
                turn=turn.turn,
                deadline=_as_aware(turn.deadline_at),
                agent_id=agent_id,
            )
        )

    chosen = select_next_turn(candidates)
    if chosen is None:
        return {"status": "waiting", "next_poll_after_seconds": _POLL_WHEN_WAITING}

    agent = agent_by_id[chosen.agent_id]
    player = player_by_key[(chosen.agent_id, chosen.match_id)]
    version = version_by_agent_id[chosen.agent_id]
    match = (
        await db.execute(select(Match).where(Match.id == chosen.match_id))
    ).scalar_one()
    turn = latest_turn_by_match[chosen.match_id]
    all_players = (
        (await db.execute(select(Player).where(Player.match_id == match.id))).scalars().all()
    )
    seat_name_by_agent_id = {p.agent_id: p.seat_name for p in all_players}
    history = _group_into_turns(await _load_public_action_records(db, match.id, all_players))
    scoreboard = [
        {
            "agent_id": seat_name_by_agent_id[p.agent_id],
            "round_score": p.current_round_score,
            "round_wins": p.total_round_wins,
        }
        for p in sorted(all_players, key=lambda p: (-p.current_round_score, p.seat_name))
    ]
    static = {
        "match_id": match.id,
        "game_id": match.id,
        "game": match.game,
        "rules_version": match.rules_version,
        "rules": get_game_module(match.game).rules_text(
            match.total_rounds, match.turns_per_round
        ),
        "total_rounds": match.total_rounds,
        "turns_per_round": match.turns_per_round,
        "your_agent_id": seat_name_by_agent_id[player.agent_id],
        "all_agent_ids": sorted(seat_name_by_agent_id.values()),
        "your_strategy": version.strategy_text,
    }
    current = await _build_current_turn(db, turn)
    return {
        "status": "your_turn",
        "match_id": match.id,
        "game": match.game,
        "agent_id": agent.id,
        "agent_name": agent.name,
        "model": version.model,
        "strategy": version.strategy_text,
        "version_no": version.version_no,
        "seat_name": seat_name_by_agent_id[player.agent_id],
        "turn_token": turn.turn_token,
        "agent_turn_token": _agent_turn_token(turn.turn_token, agent.id, match.id),
        "static": static,
        "history": history,
        "scoreboard": scoreboard,
        "current": current,
    }


class _ReportPidRequest(BaseModel):
    pid: int


@router.post("/report-pid", status_code=204)
async def report_pid(
    body: _ReportPidRequest,
    connection: Annotated[Connection, Depends(require_connection)],
    db: DbSession,
) -> None:
    """Store the runner's OS process ID so the operator can kill a stuck process."""
    connection.runner_pid = body.pid
    await db.commit()
