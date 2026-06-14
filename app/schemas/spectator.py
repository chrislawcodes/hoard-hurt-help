"""Pydantic schemas for the public spectator API.

Excludes strategy prompts unconditionally.
"""

from datetime import datetime

from pydantic import BaseModel

from app.schemas.agent import ScoreboardRow
from app.schemas.agent import MatchIdEnvelope


class SpectatorAgent(BaseModel):
    agent_id: str
    model_self_report: str | None = None


class SpectatorMessage(BaseModel):
    agent_id: str
    message: str


class SpectatorAction(BaseModel):
    agent_id: str
    action: str
    target_id: str | None
    quantity: int | None = None
    face: int | None = None
    points_delta: int


class SpectatorTurn(BaseModel):
    round: int
    turn: int
    messages: list[SpectatorMessage]
    actions: list[SpectatorAction]


class SpectatorState(MatchIdEnvelope):
    name: str
    state: str
    scheduled_start: datetime
    started_at: datetime | None
    completed_at: datetime | None
    current_round: int
    current_turn: int
    per_turn_deadline_seconds: int
    agents: list[SpectatorAgent]
    scoreboard: list[ScoreboardRow]
    history: list[SpectatorTurn]
    public_state: dict | None = None
