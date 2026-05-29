"""Pydantic schemas for the Agent API.

These shapes are documented in SPEC.md §1.1 and contracts/api.yaml.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Action = Literal["HOARD", "HELP", "HURT"]


# --- Join ---


class JoinRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_]+$")
    strategy_prompt: str = Field(min_length=1, max_length=2000)
    model_self_report: str | None = Field(default=None, max_length=200)


class JoinResponse(BaseModel):
    game_id: str
    agent_id: str
    agent_key: str
    poll_url: str
    submit_url: str
    scheduled_start: datetime
    per_turn_deadline_seconds: int


# --- Poll response shapes ---


class WaitingResponse(BaseModel):
    status: Literal["waiting"] = "waiting"
    reason: Literal["turn_not_open", "already_submitted", "game_not_started", "game_over"]
    game_state: str
    current_round: int = 0
    current_turn: int = 0
    next_poll_after_seconds: int = 2


class ScoreboardRow(BaseModel):
    agent_id: str
    round_score: int
    round_wins: float


class HistoryAction(BaseModel):
    agent_id: str
    action: Action
    target_id: str | None
    message: str
    points_delta: int


class HistoryTurn(BaseModel):
    round: int
    turn: int
    actions: list[HistoryAction]


class TurnStatic(BaseModel):
    game_id: str
    rules_version: str
    rules: str
    total_rounds: int
    turns_per_round: int
    your_agent_id: str
    all_agent_ids: list[str]
    your_strategy: str | None = None


class TurnDynamic(BaseModel):
    current_round: int
    current_turn: int
    deadline: datetime
    turn_token: str
    scoreboard: list[ScoreboardRow]
    history: list[HistoryTurn]


class YourTurnResponse(BaseModel):
    status: Literal["your_turn"] = "your_turn"
    static: TurnStatic
    dynamic: TurnDynamic


class GameCompletedResponse(BaseModel):
    status: Literal["game_completed"] = "game_completed"
    winner_agent_id: str | None
    final_standings: list[dict]


# --- Submit ---


class SubmitRequest(BaseModel):
    turn_token: str
    action: Action
    target_id: str | None = None
    message: str = Field(default="", max_length=500)


class SubmitResponse(BaseModel):
    status: Literal["accepted"] = "accepted"
    received_at: datetime
    turn_will_resolve_at: datetime


# --- State (agent-flavored) ---


class AgentStateResponse(BaseModel):
    game_id: str
    game_state: str
    current_round: int
    current_turn: int
    deadline: datetime | None
    you_have_submitted_current_turn: bool
    scoreboard: list[ScoreboardRow]
    all_agent_ids: list[str]


# --- Leave ---


class LeaveResponse(BaseModel):
    status: Literal["left"] = "left"
    game_state: str
    effective_at: datetime
