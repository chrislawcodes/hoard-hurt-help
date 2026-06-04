"""Player table — one row per (match, user) participation."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Player(Base):
    __tablename__ = "players"
    # Agent names stay unique within a match. A bot has at most one player per
    # match (UNIQUE(bot_id, match_id)), so a (bot, match) pair maps to exactly
    # one player; a user fields multiple agents in a match by running multiple
    # bots. Auth is by the owning bot's stable key, not a per-player key.
    __table_args__ = (
        UniqueConstraint("match_id", "agent_id", name="uq_players_match_id_agent_id"),
        UniqueConstraint("bot_id", "match_id", name="uq_players_bot_id_match_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    bot_id: Mapped[int] = mapped_column(
        ForeignKey("bots.id"), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(32), nullable=False)
    model_self_report: Mapped[str | None] = mapped_column(String(200), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_round_wins: Mapped[float] = mapped_column(default=0.0, nullable=False)
    total_round_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_round_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
