"""Public spectator JSON API. Never returns strategy prompts."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from sqlalchemy import select

from app.deps import DbSession
from app.models.match import Match
from app.read_models.matches import count_players, load_resolved_turn_rows, load_scoreboard
from app.schemas.spectator import (
    SpectatorAction,
    SpectatorAgent,
    SpectatorMessage,
    SpectatorState,
    SpectatorTurn,
)

router = APIRouter(tags=["spectator"])


@router.get("/api/matches")
@router.get("/api/games")
async def list_games_public(
    db: DbSession,
    state: str | None = None,
) -> list[dict]:
    """Public list of games. Filterable by `state` query param.

    Excludes strategy prompts. Returned in scheduled_start desc order.
    """
    q = select(Match).order_by(Match.scheduled_start.desc())
    if state:
        q = q.where(Match.state == state)
    games = (await db.execute(q)).scalars().all()
    out = []
    for g in games:
        out.append(
            {
                "id": g.id,
                "name": g.name,
                "state": g.state.value,
                "scheduled_start": g.scheduled_start.isoformat(),
                "started_at": g.started_at.isoformat() if g.started_at else None,
                "completed_at": g.completed_at.isoformat() if g.completed_at else None,
                "min_players": g.min_players,
                "max_players": g.max_players,
                "per_turn_deadline_seconds": g.per_turn_deadline_seconds,
                "current_round": g.current_round,
                "current_turn": g.current_turn,
                "player_count": await count_players(db, g.id),
            }
        )
    return out


@router.get("/api/spectator/matches/{match_id}/state", response_model=SpectatorState)
@router.get("/api/spectator/games/{match_id}/state", response_model=SpectatorState)
async def public_state(
    match_id: Annotated[str, Path()],
    db: DbSession,
) -> SpectatorState:
    g = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404)
    turn_rows = await load_resolved_turn_rows(db, match_id)
    players = turn_rows.players
    players_by_id = turn_rows.players_by_id
    turns = turn_rows.turns
    messages_by_turn = turn_rows.messages_by_turn
    subs_by_turn = turn_rows.submissions_by_turn

    history: list[SpectatorTurn] = []
    for t in turns:
        subs = subs_by_turn.get(t.id, [])
        turn_messages = messages_by_turn.get(t.id, [])
        messages: list[SpectatorMessage]
        if turn_messages:
            messages = [
                SpectatorMessage(
                    agent_id=players_by_id[msg.player_id].agent_id,
                    message=msg.text,
                )
                for msg in turn_messages
                if msg.player_id in players_by_id
            ]
        else:
            messages = [
                SpectatorMessage(
                    agent_id=players_by_id[s.player_id].agent_id,
                    message=s.message,
                )
                for s in subs
                if s.player_id in players_by_id
            ]
        actions: list[SpectatorAction] = []
        for s in subs:
            actor = players_by_id.get(s.player_id)
            target = players_by_id.get(s.target_player_id) if s.target_player_id else None
            if not actor:
                continue
            actions.append(
                SpectatorAction(
                    agent_id=actor.agent_id,
                    action=s.action,
                    target_id=target.agent_id if target else None,
                    points_delta=s.points_delta,
                )
            )
        history.append(SpectatorTurn(round=t.round, turn=t.turn, messages=messages, actions=actions))
    return SpectatorState(
        match_id=g.id,
        name=g.name,
        state=g.state.value,
        scheduled_start=g.scheduled_start,
        started_at=g.started_at,
        completed_at=g.completed_at,
        current_round=g.current_round,
        current_turn=g.current_turn,
        per_turn_deadline_seconds=g.per_turn_deadline_seconds,
        agents=[
            SpectatorAgent(agent_id=p.agent_id, model_self_report=p.model_self_report)
            for p in players
        ],
        scoreboard=await load_scoreboard(db, match_id),
        history=history,
    )
