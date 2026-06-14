"""Pydantic schemas for admin endpoints."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class CreateGameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scheduled_start: datetime
    game_type: str = Field(default="hoard-hurt-help", max_length=64)
    min_players: int = Field(default=6, ge=3, le=100)
    max_players: int = Field(default=10, ge=3, le=100)
    per_turn_deadline_seconds: int = Field(default=60, ge=5, le=600)
    total_rounds: int = Field(default=7, ge=3, le=20)
    turns_per_round: int = Field(default=7, ge=3, le=20)
    wild_ones: bool = True
    dice_per_player: int = Field(default=5, ge=1, le=20)

    @field_validator("max_players")
    @classmethod
    def _max_ge_min(cls, v, info):
        if "min_players" in info.data and v < info.data["min_players"]:
            raise ValueError("max_players must be >= min_players")
        return v


class GameRecord(BaseModel):
    id: str
    name: str
    state: str
    scheduled_start: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    min_players: int
    max_players: int
    per_turn_deadline_seconds: int
    current_round: int
    current_turn: int
    rules_version: str
    winner_agent_id: str | None = None


class CancelResponse(BaseModel):
    status: str = "cancelled"
