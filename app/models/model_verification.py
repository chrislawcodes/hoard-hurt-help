"""ModelVerification — the result of the connector's fail-fast model test.

Mirrors the provider-readiness idea one level down: for a given connection's CLI
login, can it actually run a given model? Keyed by (connection, provider, model)
because a login either can or cannot run a model regardless of which agent uses
it. The connector verifies each model it would run and reports the outcome here;
the website surfaces it and the join flow warns on it.

Lives in its own table (not the per-(connection, provider) connection_providers
row, which physically can't hold multiple models per provider).
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enum_types import FlexibleEnumType


class ModelVerificationStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    CHECKING = "checking"
    VERIFIED = "verified"
    FAILED = "failed"  # sticky: the login can't run this model (fix it)
    TIMEOUT = "timeout"  # retryable: a transient/timeout/unclassified failure


class ModelVerification(Base):
    __tablename__ = "model_verifications"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "provider",
            "model",
            name="uq_model_verifications_conn_provider_model",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("connections.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ModelVerificationStatus] = mapped_column(
        FlexibleEnumType(ModelVerificationStatus, length=16),
        nullable=False,
        default=ModelVerificationStatus.UNKNOWN,
    )
    # Bounded + sanitized (no paths/tokens) before storage — see sanitize_error.
    error_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Count of consecutive timeouts; at the escalation threshold the status is
    # stored as FAILED so it never sits in a silent retry loop (FR-013).
    consecutive_timeouts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
