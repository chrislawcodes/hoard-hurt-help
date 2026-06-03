"""Turn + TurnSubmission tables."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (
        UniqueConstraint("match_id", "round", "turn", name="uq_turns_match_id_round_turn"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.id"), nullable=False, index=True
    )
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    turn: Mapped[int] = mapped_column(Integer, nullable=False)
    turn_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    phase: Mapped[str] = mapped_column(
        String(8), nullable=False, default="talk", server_default="talk"
    )
    talk_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TurnSubmission(Base):
    __tablename__ = "turn_submissions"
    __table_args__ = (
        UniqueConstraint(
            "turn_id", "player_id", name="uq_turn_submissions_turn_id_player_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    turn_id: Mapped[int] = mapped_column(
        ForeignKey("turns.id"), nullable=False, index=True
    )
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    target_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id"), nullable=True
    )
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    thinking: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    points_delta: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    round_score_after: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    was_defaulted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TurnMessage(Base):
    __tablename__ = "turn_messages"
    __table_args__ = (
        UniqueConstraint("turn_id", "player_id", name="uq_turn_messages_turn_id_player_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    turn_id: Mapped[int] = mapped_column(
        ForeignKey("turns.id"), nullable=False, index=True
    )
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    thinking: Mapped[str] = mapped_column(Text, default="", nullable=False)
    was_defaulted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
