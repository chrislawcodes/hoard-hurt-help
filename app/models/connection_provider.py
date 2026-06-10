"""Per-connection provider toggle rows."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.connection import ConnectionProvider as ConnectionProviderEnum
from app.models.enum_types import FlexibleEnumType


class ConnectionProvider(Base):
    __tablename__ = "connection_providers"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "provider",
            name="uq_connection_providers_connection_id_provider",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[ConnectionProviderEnum] = mapped_column(
        FlexibleEnumType(ConnectionProviderEnum, length=16),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa.false(),
    )
    detected: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa.false(),
    )
    detected_detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
