"""Player table — one row per match participation."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (
        UniqueConstraint("match_id", "seat_name", name="uq_players_match_id_seat_name"),
        UniqueConstraint("agent_id", "match_id", name="uq_players_agent_id_match_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id"), nullable=False, index=True
    )
    served_by_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("connections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    served_pinned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    agent_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_versions.id"),
        nullable=True,
        index=True,
    )
    seat_name: Mapped[str] = mapped_column(String(40), nullable=False)
    model_self_report: Mapped[str | None] = mapped_column(String(200), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_round_wins: Mapped[float] = mapped_column(default=0.0, nullable=False)
    total_round_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_round_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
