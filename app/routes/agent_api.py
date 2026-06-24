"""Agent API - the HTTP endpoints player AIs call."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.deps import DbSession, require_agent_player, require_connection
from app.engine.agent_play import (
    chat_transcript,
    get_agent_state,
    leave_match,
    opponent_history,
    poll_turn,
    standings,
    submit_action,
    submit_talk,
    turn_detail,
)
from app.models.connection import Connection
from app.models.player import Player
from app.schemas.agent import (
    AgentStateResponse,
    ChatTranscriptResponse,
    FullStandingsResponse,
    LeaveResponse,
    MessageRequest,
    MessageResponse,
    OpponentHistoryResponse,
    SubmitRequest,
    SubmitResponse,
    TalkWindowClosedResponse,
    TurnDetailResponse,
    WaitingResponse,
    YourTurnResponse,
)

router = APIRouter(tags=["agent"])

# Per-bot rate-limit state. Tests monkeypatch these dicts directly, so keep them
# at module scope and pass them into the shared service.
_last_poll: dict[int, float] = {}
_last_pull: dict[tuple[int, str], float] = {}


@router.get("/turn")
async def agent_poll(
    match_id: Annotated[str, Path()],
    player: Annotated[Player, Depends(require_agent_player)],
    db: DbSession,
) -> WaitingResponse | YourTurnResponse:
    return await poll_turn(db, match_id=match_id, player=player, rate_state=_last_poll)


@router.post(
    "/message",
    response_model=MessageResponse | TalkWindowClosedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def agent_message(
    match_id: Annotated[str, Path()],
    body: MessageRequest,
    agent_turn_token: Annotated[str, Query()],
    db: DbSession,
    player: Annotated[Player, Depends(require_agent_player)],
) -> MessageResponse | TalkWindowClosedResponse:
    return await submit_talk(
        db,
        match_id=match_id,
        player=player,
        agent_turn_token=agent_turn_token,
        turn_token=body.turn_token,
        message=body.message,
        thinking=body.thinking,
        is_connector_fallback=body.is_connector_fallback,
    )


@router.post("/submit", response_model=SubmitResponse, status_code=status.HTTP_202_ACCEPTED)
async def agent_submit(
    match_id: Annotated[str, Path()],
    body: SubmitRequest,
    agent_turn_token: Annotated[str, Query()],
    db: DbSession,
    player: Annotated[Player, Depends(require_agent_player)],
    connection: Annotated[Connection, Depends(require_connection)],
) -> SubmitResponse:
    return await submit_action(
        db,
        match_id=match_id,
        player=player,
        connection=connection,
        agent_turn_token=agent_turn_token,
        turn_token=body.turn_token,
        action=body.action,
        target_id=body.target_id,
        move=body.move,
        message=body.message,
        thinking=body.thinking,
        is_connector_fallback=body.is_connector_fallback,
    )


@router.get("/state", response_model=AgentStateResponse)
async def agent_state(
    match_id: Annotated[str, Path()],
    db: DbSession,
    player: Annotated[Player, Depends(require_agent_player)],
) -> AgentStateResponse:
    return await get_agent_state(db, match_id=match_id, player=player)


@router.post("/leave", response_model=LeaveResponse)
async def agent_leave(
    match_id: Annotated[str, Path()],
    agent_turn_token: Annotated[str, Query()],
    db: DbSession,
    player: Annotated[Player, Depends(require_agent_player)],
) -> LeaveResponse:
    return await leave_match(
        db,
        match_id=match_id,
        agent_turn_token=agent_turn_token,
        player=player,
    )


@router.get("/history/opponents/{opponent_id}", response_model=OpponentHistoryResponse)
async def agent_opponent_history(
    match_id: Annotated[str, Path()],
    opponent_id: Annotated[str, Path()],
    db: DbSession,
    player: Annotated[Player, Depends(require_agent_player)],
) -> OpponentHistoryResponse:
    return await opponent_history(
        db,
        match_id=match_id,
        opponent_id=opponent_id,
        player=player,
        rate_state=_last_pull,
    )


@router.get("/chat", response_model=ChatTranscriptResponse)
async def agent_chat(
    match_id: Annotated[str, Path()],
    db: DbSession,
    player: Annotated[Player, Depends(require_agent_player)],
    since: Annotated[str | None, Query()] = None,
) -> ChatTranscriptResponse:
    return await chat_transcript(
        db,
        match_id=match_id,
        player=player,
        rate_state=_last_pull,
        since=since,
    )


@router.get("/turns/{round}/{turn}", response_model=TurnDetailResponse)
async def agent_turn_detail(
    match_id: Annotated[str, Path()],
    round: Annotated[int, Path()],
    turn: Annotated[int, Path()],
    db: DbSession,
    player: Annotated[Player, Depends(require_agent_player)],
) -> TurnDetailResponse:
    return await turn_detail(
        db,
        match_id=match_id,
        round=round,
        turn=turn,
        player=player,
        rate_state=_last_pull,
    )


@router.get("/standings", response_model=FullStandingsResponse)
async def agent_standings(
    match_id: Annotated[str, Path()],
    db: DbSession,
    player: Annotated[Player, Depends(require_agent_player)],
) -> FullStandingsResponse:
    return await standings(
        db,
        match_id=match_id,
        player=player,
        rate_state=_last_pull,
    )
