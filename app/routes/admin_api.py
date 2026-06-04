"""Admin JSON API: create/cancel games, export data."""

import csv
import io
import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.deps import DbSession, require_admin
from app.engine.tokens import generate_match_id
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.strategy_prompt import StrategyPrompt
from app.models.turn import Turn, TurnSubmission
from app.models.user import User
from app.schemas.admin import CancelResponse, CreateGameRequest, GameRecord

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/matches", response_model=GameRecord, status_code=status.HTTP_201_CREATED)
@router.post("/games", response_model=GameRecord, status_code=status.HTTP_201_CREATED)
async def create_game(
    body: CreateGameRequest,
    db: DbSession,
    _: Annotated[User, Depends(require_admin)],
) -> GameRecord:
    if body.scheduled_start <= datetime.now(timezone.utc):
        raise HTTPException(400, detail="scheduled_start must be in the future.")
    # Allocate the next M_NNNN id.
    existing_ids = (await db.execute(select(Match.id))).scalars().all()
    n = max((int(x.split("_")[1]) for x in existing_ids if x.startswith("M_")), default=0) + 1
    g = Match(
        id=generate_match_id(n),
        name=body.name,
        state=GameState.REGISTERING,
        scheduled_start=body.scheduled_start,
        min_players=body.min_players,
        max_players=body.max_players,
        per_turn_deadline_seconds=body.per_turn_deadline_seconds,
        total_rounds=body.total_rounds,
        turns_per_round=body.turns_per_round,
    )
    db.add(g)
    await db.commit()
    await db.refresh(g)
    return GameRecord(
        id=g.id,
        name=g.name,
        state=g.state.value,
        scheduled_start=g.scheduled_start,
        started_at=g.started_at,
        completed_at=g.completed_at,
        cancelled_at=g.cancelled_at,
        min_players=g.min_players,
        max_players=g.max_players,
        per_turn_deadline_seconds=g.per_turn_deadline_seconds,
        current_round=g.current_round,
        current_turn=g.current_turn,
        rules_version=g.rules_version,
    )


@router.post("/matches/{match_id}/cancel", response_model=CancelResponse)
@router.post("/games/{match_id}/cancel", response_model=CancelResponse)
async def cancel_game(
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_admin)],
) -> CancelResponse:
    g = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404)
    if g.state == GameState.ACTIVE:
        raise HTTPException(409, detail="Match already started.")
    if g.state in (GameState.COMPLETED, GameState.CANCELLED):
        raise HTTPException(409, detail="Match already ended.")
    g.state = GameState.CANCELLED
    g.cancelled_at = datetime.now(timezone.utc)
    await db.commit()
    return CancelResponse()


_EXPORT_COLUMNS = [
    "match_id",
    "round",
    "turn",
    "agent_id",
    "action",
    "target_id",
    "message",
    "points_delta",
    "round_score_after",
    "submitted_at",
    "was_defaulted",
]


@router.get("/matches/{match_id}/export.csv")
@router.get("/games/{match_id}/export.csv")
async def export_csv(
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_admin)],
):
    rows = await _gather_export_rows(db, match_id)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(_EXPORT_COLUMNS)
    for r in rows:
        w.writerow([r[k] for k in _EXPORT_COLUMNS])
    return StreamingResponse(
        iter([out.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{match_id}.csv"'},
    )


@router.get("/matches/{match_id}/export.json")
@router.get("/games/{match_id}/export.json")
async def export_json(
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_admin)],
):
    g = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404)
    players = (
        (await db.execute(select(Player).where(Player.match_id == match_id))).scalars().all()
    )
    players_payload = []
    for p in players:
        prompt = (
            await db.execute(
                select(StrategyPrompt)
                .where(StrategyPrompt.player_id == p.id)
                .order_by(StrategyPrompt.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        players_payload.append(
            {
                "agent_id": p.agent_id,
                "model_self_report": p.model_self_report,
                "total_round_wins": p.total_round_wins,
                "total_round_score": p.total_round_score,
                "strategy_prompt": prompt.prompt_text if prompt else None,
            }
        )
    rows = await _gather_export_rows(db, match_id)
    payload = {
        "game": {
            "id": g.id,
            "name": g.name,
            "state": g.state.value,
            "scheduled_start": g.scheduled_start.isoformat(),
            "started_at": g.started_at.isoformat() if g.started_at else None,
            "completed_at": g.completed_at.isoformat() if g.completed_at else None,
            "rules_version": g.rules_version,
        },
        "players": players_payload,
        "submissions": rows,
    }
    return StreamingResponse(
        iter([json.dumps(payload, indent=2)]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{match_id}.json"'},
    )


async def _gather_export_rows(db, match_id: str) -> list[dict]:
    players = (
        (await db.execute(select(Player).where(Player.match_id == match_id))).scalars().all()
    )
    players_by_id = {p.id: p for p in players}
    turns = (
        (
            await db.execute(
                select(Turn).where(Turn.match_id == match_id).order_by(Turn.round, Turn.turn)
            )
        )
        .scalars()
        .all()
    )
    rows = []
    for t in turns:
        subs = (
            (await db.execute(select(TurnSubmission).where(TurnSubmission.turn_id == t.id)))
            .scalars()
            .all()
        )
        for s in subs:
            actor = players_by_id.get(s.player_id)
            target = players_by_id.get(s.target_player_id) if s.target_player_id else None
            if not actor:
                continue
            rows.append(
                {
                    "match_id": match_id,
                    "round": t.round,
                    "turn": t.turn,
                    "agent_id": actor.agent_id,
                    "action": s.action,
                    "target_id": target.agent_id if target else "",
                    "message": s.message,
                    "points_delta": s.points_delta,
                    "round_score_after": s.round_score_after,
                    "submitted_at": s.submitted_at.isoformat() if s.submitted_at else "",
                    "was_defaulted": s.was_defaulted,
                }
            )
    return rows
