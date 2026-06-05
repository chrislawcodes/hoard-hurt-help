"""Per-title game state — generic, module-owned, opaque to the platform.

Hidden-information and stateful games (Liar's Dice is the first) need to persist
state the PD-shaped `turn_submissions` / `players` columns cannot hold: public
match state (e.g. the standing bid, whose turn) and per-player *private* state
(e.g. a player's hidden dice). These two tables are deliberately game-agnostic
JSON blobs the game module reads and writes; the platform never inspects them.

Prisoner's Dilemma writes neither — its state lives in the existing columns. So
these tables are inert for PD and exist for game #2 onward.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base


class MatchState(Base):
    """Public, module-owned game state for one match (one row per match)."""

    __tablename__ = "match_state"

    match_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("matches.id"), primary_key=True
    )
    # MutableDict so in-place edits (state_json["x"] = ...) mark the column dirty
    # and persist; a raw JSON column would silently drop in-place mutations.
    state_json: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class PlayerState(Base):
    """Private, module-owned per-player state for one match (one row per player)."""

    __tablename__ = "player_state"

    match_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("matches.id"), primary_key=True
    )
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id"), primary_key=True
    )
    state_json: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
