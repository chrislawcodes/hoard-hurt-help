"""Public spectator JSON API. Never returns strategy prompts."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from sqlalchemy import select

from app.deps import DbSession
from app.models.game import Game
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission
from app.schemas.agent import HistoryAction, HistoryTurn, ScoreboardRow
from app.schemas.spectator import SpectatorAgent, SpectatorState

router = APIRouter(prefix="/api/spectator/games/{game_id}", tags=["spectator"])


@router.get("/state", response_model=SpectatorState)
async def public_state(
    game_id: Annotated[str, Path()],
    db: DbSession,
) -> SpectatorState:
    g = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404)
    players = (
        (await db.execute(select(Player).where(Player.game_id == game_id))).scalars().all()
    )
    players_by_id = {p.id: p for p in players}
    scoreboard = [
        ScoreboardRow(
            agent_id=p.agent_id,
            round_score=p.current_round_score,
            round_wins=p.total_round_wins,
        )
        for p in players
    ]
    turns = (
        (
            await db.execute(
                select(Turn)
                .where(Turn.game_id == game_id, Turn.resolved_at.is_not(None))
                .order_by(Turn.round, Turn.turn)
            )
        )
        .scalars()
        .all()
    )
    history: list[HistoryTurn] = []
    for t in turns:
        subs = (
            (await db.execute(select(TurnSubmission).where(TurnSubmission.turn_id == t.id)))
            .scalars()
            .all()
        )
        actions = []
        for s in subs:
            actor = players_by_id.get(s.player_id)
            target = players_by_id.get(s.target_player_id) if s.target_player_id else None
            if not actor:
                continue
            actions.append(
                HistoryAction(
                    agent_id=actor.agent_id,
                    action=s.action,  # type: ignore[arg-type]
                    target_id=target.agent_id if target else None,
                    message=s.message,
                    points_delta=s.points_delta,
                )
            )
        history.append(HistoryTurn(round=t.round, turn=t.turn, actions=actions))
    return SpectatorState(
        game_id=g.id,
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
        scoreboard=scoreboard,
        history=history,
    )
