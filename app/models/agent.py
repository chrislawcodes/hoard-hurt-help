"""Agent table — a per-game competitor identity."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.models.base import Base
from app.models.enum_types import FlexibleEnumType


class AgentKind(str, enum.Enum):
    AI = "ai"
    BOT = "bot"


class AgentStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_agents_user_id_name"),
        UniqueConstraint(
            "user_id",
            "bot_profile_id",
            name="uq_agents_user_id_bot_profile_id",
        ),
        CheckConstraint(
            "kind != 'bot' OR connection_id IS NULL",
            name="ck_agents_bot_connection_null",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("connections.id"),
        nullable=True,
        index=True,
    )
    kind: Mapped[AgentKind] = mapped_column(
        FlexibleEnumType(AgentKind, length=16),
        nullable=False,
        default=AgentKind.AI,
        server_default=AgentKind.AI.value,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    game: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="hoard-hurt-help",
        index=True,
    )
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "agent_versions.id",
            name="fk_agents_current_version_id_agent_versions",
            use_alter=True,
        ),
        nullable=True,
    )
    status: Mapped[AgentStatus] = mapped_column(
        FlexibleEnumType(AgentStatus, length=16),
        nullable=False,
        default=AgentStatus.ACTIVE,
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    bot_profile_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    bot_profile_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    bot_strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bot_truthfulness: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bot_trust_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bot_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bot_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bot_fixture_pack: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @validates("kind", "connection_id")
    def _validate_bot_connection(
        self, key: str, value: AgentKind | int | None
    ) -> AgentKind | int | None:
        """Bots never carry a connection; AI agents may be detached."""
        kind = value if key == "kind" else self.kind
        connection_id = value if key == "connection_id" else self.connection_id
        if kind == AgentKind.BOT and connection_id is not None:
            raise ValueError("bot agents cannot have a connection")
        return value
