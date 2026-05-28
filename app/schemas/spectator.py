"""Pydantic schemas for the public spectator API.

Excludes strategy prompts unconditionally.
"""

from datetime import datetime

from pydantic import BaseModel

from app.schemas.agent import HistoryTurn, ScoreboardRow


class SpectatorAgent(BaseModel):
    agent_id: str
    model_self_report: str | None = None


class SpectatorState(BaseModel):
    game_id: str
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
    history: list[HistoryTurn]
