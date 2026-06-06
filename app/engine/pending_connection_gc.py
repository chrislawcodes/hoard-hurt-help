"""Garbage collection for abandoned pending connections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import cast

from sqlalchemy import delete
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connection import Connection, ConnectionStatus

_PENDING_MAX_AGE = timedelta(hours=24)


async def gc_pending_connections(
    db: AsyncSession, *, now: datetime | None = None
) -> int:
    """Delete pending connections that never connected within 24 hours."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - _PENDING_MAX_AGE
    result = await db.execute(
        delete(Connection).where(
            Connection.status == ConnectionStatus.PENDING,
            Connection.first_connected_at.is_(None),
            Connection.created_at < cutoff,
        )
    )
    await db.commit()
    return cast(CursorResult, result).rowcount or 0
