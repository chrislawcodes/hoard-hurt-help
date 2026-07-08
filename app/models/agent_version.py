"""AgentVersion table — immutable-once-played strategy snapshots.

``model`` is legacy and now optional: agents are decoupled from a fixed AI
model/provider (they are just name + strategy). New versions leave it NULL; the
provider that actually played a match is recorded on the player row instead
(``Player.played_provider``).
"""

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
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    strategy_text: Mapped[str] = mapped_column(Text, nullable=False)
    # The owner's short "what did you change" label for this version. Written
    # with a strategy save (overwritten on an in-place draft edit, set fresh on
    # a fork); purely descriptive — never read by play.
    note: Mapped[str | None] = mapped_column(String(140), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    frozen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
