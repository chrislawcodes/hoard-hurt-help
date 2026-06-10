"""Connection table — a user's AI login, provider, and runner state."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enum_types import FlexibleEnumType


class ConnectionProvider(str, enum.Enum):
    CLAUDE = "claude"
    GEMINI = "gemini"
    OPENAI = "openai"
    HERMES = "hermes"
    OPENCLAW = "openclaw"


class ConnectionStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    PAUSED = "paused"


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    nickname: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # DB column is nullable (migration 0026) so future machine-style connections
    # can leave it NULL, but the Python type stays non-None until slice 5
    # introduces those NULL rows and updates every consumer together. Every row
    # that exists today is backfilled with its legacy provider, so non-None is
    # accurate for now.
    provider: Mapped[ConnectionProvider] = mapped_column(
        FlexibleEnumType(ConnectionProvider, length=16),
        nullable=True,
    )
    key_lookup: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    prev_key_lookup: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    key_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[ConnectionStatus] = mapped_column(
        FlexibleEnumType(ConnectionStatus, length=16),
        nullable=False,
        default=ConnectionStatus.PENDING,
    )
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    paused_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    first_connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    runner_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_concurrent_games: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
    )
    stall_threshold: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
