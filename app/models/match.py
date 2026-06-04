"""Match table — one row per match (a single play), with lifecycle state.

Renamed from Game (feature 009): a "match" is one play start→finish; "game" now
means the title/module in app/games/. The lifecycle enum keeps the name
`GameState` deliberately — it names the state machine, not a single play, and is
not part of the overloaded game/match vocabulary.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enum_types import FlexibleEnumType


class GameState(str, enum.Enum):
    SCHEDULED = "scheduled"
    REGISTERING = "registering"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class MatchKind(str, enum.Enum):
    MANUAL = "manual"
    PRACTICE_ARENA = "practice_arena"
    AUTO_SCHEDULED = "auto_scheduled"


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Which game (title/module in app/games/) this match plays. Defaults to PD;
    # a future game would set its own slug. Registry key string is unchanged;
    # only the column moved game_type→game. See specs/004-game-framework/.
    game: Mapped[str] = mapped_column(
        String(64), nullable=False, default="hoard-hurt-help", index=True
    )
    state: Mapped[GameState] = mapped_column(
        FlexibleEnumType(GameState, length=32),
        nullable=False,
        index=True,
    )
    scheduled_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    min_players: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    max_players: Mapped[int] = mapped_column(
        Integer, default=20, server_default="20", nullable=False
    )
    per_turn_deadline_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    total_rounds: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    turns_per_round: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    current_round: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_turn: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Highest round number already awarded (round-wins + round-score folded into
    # player totals). Guards award_round against double-counting when the loop
    # resumes at an already-finished round after a mid-game restart. 0 = none.
    rounds_awarded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rules_version: Mapped[str] = mapped_column(String(16), default="v1", nullable=False)
    winner_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", use_alter=True, name="fk_matches_winner_player_id_players"),
        nullable=True,
    )
    match_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual", server_default="manual"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_matches_match_kind", "match_kind"),)
