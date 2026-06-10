"""Draft connection setup state before the AI provider connects."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.connection import ConnectionProvider
from app.models.enum_types import FlexibleEnumType


class ConnectionSetup(Base):
    __tablename__ = "connection_setups"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    # DB column nullable (migration 0026) for future machine-style setups; the
    # Python type stays non-None until slice 5 creates NULL drafts and updates
    # the resume/label paths together.
    provider: Mapped[ConnectionProvider] = mapped_column(
        FlexibleEnumType(ConnectionProvider, length=16),
        nullable=True,
    )
    nickname: Mapped[str | None] = mapped_column(String(60), nullable=True)
    key_lookup: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    key_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("connections.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
