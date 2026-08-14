"""Durable record of how far a user got on the way to actually playing.

One row the first time a user reaches a milestone, and never again. The table is
append-only: nothing in the app updates or deletes a row once written.

Why this exists rather than deriving the same answers from the tables that
already hold them: the app destroys the evidence. Incomplete connection setups
are garbage collected, held seats are deleted within ~15 minutes, agents with no
game history are hard-deleted rather than archived, and an admin match delete
removes players and turns. Those are precisely the abandonment cases a drop-off
report needs to count, so the count has to be recorded when it happens.

``returned`` is deliberately NOT a milestone. Whether someone came back on a
second day depends on the timezone the reader is looking in, so it is derived
from ``turn_submissions`` at read time instead of frozen at write time.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enum_types import FlexibleEnumType


class MilestoneKind(str, enum.Enum):
    """A rung on the path from signing up to playing regularly.

    These are not a strict sequence. A user reaches them in whatever order their
    path takes: someone connecting through MCP gets a live connection before they
    have an agent or a handle, and a human player never reaches ``ai_connected``
    at all. Reading them as a waterfall is what made three earlier designs report
    real players as having quit.
    """

    SIGNED_UP = "signed_up"
    PICKED_HANDLE = "picked_handle"
    # Split by seat kind rather than carried on one milestone with a kind column:
    # manual play is the default for a brand-new user, so a single write-once row
    # would record "human" for almost everyone and leave the AI-agent population
    # looking empty.
    SET_UP_AI_AGENT = "set_up_ai_agent"
    SET_UP_HUMAN_PLAY = "set_up_human_play"
    AI_CONNECTED = "ai_connected"
    JOINED_MATCH = "joined_match"
    PLAYED_TURN = "played_turn"


class UserMilestone(Base):
    __tablename__ = "user_milestones"
    __table_args__ = (
        UniqueConstraint("user_id", "milestone", name="uq_user_milestones_user_id"),
        Index("ix_user_milestones_milestone_reached_at", "milestone", "reached_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    milestone: Mapped[MilestoneKind] = mapped_column(
        FlexibleEnumType(MilestoneKind, length=32), nullable=False
    )
    reached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Which match the milestone happened in, where that is meaningful
    # (joined_match, played_turn). Kept so smoke-test matches can be excluded when
    # the page is read; the milestone row carries no other route back to a match.
    source_match_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
