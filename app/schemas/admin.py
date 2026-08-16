"""Pydantic schemas for admin endpoints."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.game_types import DEFAULT_GAME_TYPE
from app.games.hoard_hurt_help.rules import (
    DEFAULT_MUTUAL_HELP_MODE,
    DEFAULT_TOTAL_ROUNDS,
    DEFAULT_TURNS_PER_ROUND,
    MutualHelpMode,
)


class CreateGameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scheduled_start: datetime
    game_type: str = Field(default=DEFAULT_GAME_TYPE, max_length=64)
    min_players: int = Field(default=6, ge=3, le=100)
    max_players: int = Field(default=10, ge=3, le=100)
    per_turn_deadline_seconds: int = Field(default=60, ge=5, le=600)
    # Omitting either gets the shipped match length, same as every other create
    # path — this schema already defaults game_type to Hoard-Hurt-Help.
    total_rounds: int = Field(default=DEFAULT_TOTAL_ROUNDS, ge=3, le=20)
    turns_per_round: int = Field(default=DEFAULT_TURNS_PER_ROUND, ge=3, le=20)
    wild_ones: bool = True
    dice_per_player: int = Field(default=5, ge=1, le=20)
    # Hoard-Hurt-Help's per-match rule switch. Omitting it gets whatever the
    # platform's current default rule is, the same as every other create path.
    mutual_help_mode: str = DEFAULT_MUTUAL_HELP_MODE.value

    @field_validator("max_players")
    @classmethod
    def _max_ge_min(cls, v, info):
        if "min_players" in info.data and v < info.data["min_players"]:
            raise ValueError("max_players must be >= min_players")
        return v

    @field_validator("mutual_help_mode")
    @classmethod
    def _known_mutual_help_mode(cls, v: str) -> str:
        """Reject an unknown mode rather than letting it reach the column.

        A typo stored verbatim would mislabel which rule the match was played
        under, and nothing downstream would notice.
        """
        try:
            MutualHelpMode(v)
        except ValueError as exc:
            known = ", ".join(m.value for m in MutualHelpMode)
            raise ValueError(f"Unknown mutual_help_mode {v!r}. Known: {known}.") from exc
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
