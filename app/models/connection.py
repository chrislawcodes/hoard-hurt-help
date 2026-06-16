"""Connection table — a user's AI login, provider, and runner state."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enum_types import FlexibleEnumType

if TYPE_CHECKING:
    from app.models.user import User


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
    __table_args__ = (
        # One live MCP connection per user PER PROVIDER. An MCP client
        # speaks for exactly one provider (one client == one provider, #392), so
        # each provider the user signs in gets its own connection — never one
        # connection that accumulates several. ``provider`` is therefore set on a
        # MCP connection (it is the connection's identity), unlike the legacy
        # accumulate-providers-on-one-row model.
        Index(
            "uq_connections_mcp_user_provider_live",
            "user_id",
            "provider",
            unique=True,
            sqlite_where=text("mcp_connected_at IS NOT NULL AND deleted_at IS NULL"),
            postgresql_where=text("mcp_connected_at IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    nickname: Mapped[str | None] = mapped_column(String(60), nullable=True)
    provider: Mapped[ConnectionProvider | None] = mapped_column(
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
    mcp_connected_at: Mapped[datetime | None] = mapped_column(
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
    # The play-loop heartbeat: when the AI last polled get_next_turn. Distinct from
    # last_seen_at (which ANY authenticated call bumps, even a one-time sign-in
    # handshake) — this only advances while an AI is actually running the play loop.
    # It's the honest signal for "is this agent playing", used to gate seating.
    last_polled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    runner_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Lifetime usage counters for this connection, surfaced on the detail page so
    # an operator running interactive (MCP) play can see what it's costing them.
    # `api_call_count` counts every authenticated agent call (each is a paid model
    # call in interactive mode); `turns_played` counts real moves submitted.
    api_call_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    turns_played: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
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
    user: Mapped["User"] = relationship("User", lazy="raise")
