"""Player table — one row per (game, user) participation."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Player(Base):
    __tablename__ = "players"
    # Agent names stay unique within a game, but a single user may run several
    # bots in the same game — so there is deliberately no (game_id, user_id)
    # uniqueness. Migration 0002 drops that constraint on existing databases.
    __table_args__ = (
        UniqueConstraint("game_id", "agent_id", name="uq_players_game_id_agent_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[str] = mapped_column(
        ForeignKey("games.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    model_self_report: Mapped[str | None] = mapped_column(String(200), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_round_wins: Mapped[float] = mapped_column(default=0.0, nullable=False)
    total_round_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_round_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
