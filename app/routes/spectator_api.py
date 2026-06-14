"""Public spectator JSON API. Never returns strategy prompts."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from sqlalchemy import select

from app.deps import DbSession
from app.games import get as get_game_module
from app.models.match import Match
from app.read_models.matches import (
    count_players,
    load_match_timeline,
    load_players,
    load_scoreboard,
)
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
                "scheduled_start": g.scheduled_start.isoformat() if g.scheduled_start else None,
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
    module = get_game_module(g.game)
    players = await load_players(db, match_id)
    timeline = await load_match_timeline(db, match_id)

    history: list[SpectatorTurn] = []
    for turn in timeline:
        messages = [
            SpectatorMessage(agent_id=message.agent_id, message=message.text)
            for message in turn.messages
        ]
        actions = [
            SpectatorAction(
                agent_id=action.agent_id,
                action=action.action,
                target_id=action.target_id,
                quantity=action.quantity,
                face=action.face,
                points_delta=action.points_delta,
            )
            for action in turn.actions
        ]
        history.append(
            SpectatorTurn(
                round=turn.round,
                turn=turn.turn,
                messages=messages,
                actions=actions,
            )
        )
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
            SpectatorAgent(agent_id=p.seat_name, model_self_report=p.model_self_report)
            for p in players
        ],
        scoreboard=await load_scoreboard(db, match_id),
        history=history,
        public_state=await module.public_state_for(db, g, None),
    )
