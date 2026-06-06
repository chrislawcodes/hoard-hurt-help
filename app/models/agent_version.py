"""AgentVersion table — immutable-once-played model + strategy snapshots."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint(
            "agent_id",
            "version_no",
            name="uq_agent_versions_agent_id_version_no",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    frozen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
