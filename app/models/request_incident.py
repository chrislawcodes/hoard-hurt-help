"""Request incident rows for debugging uncaught request failures."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RequestIncident(Base):
    __tablename__ = "request_incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(
        String(16), nullable=False, unique=True, index=True
    )
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    query_string: Mapped[str | None] = mapped_column(Text(), nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    game_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    bot_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    player_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    error_type: Mapped[str] = mapped_column(String(128), nullable=False)
    error_message: Mapped[str] = mapped_column(Text(), nullable=False)
    stacktrace: Mapped[str] = mapped_column(Text(), nullable=False)
    context_json: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
