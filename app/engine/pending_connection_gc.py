"""Garbage collection for abandoned pending connections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connection import Connection, ConnectionStatus

_PENDING_MAX_AGE = timedelta(hours=24)


async def gc_pending_connections(
    db: AsyncSession, *, now: datetime | None = None
) -> int:
    """Delete pending connections that never connected within 24 hours."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - _PENDING_MAX_AGE
    stale = (
        (
            await db.execute(
                select(Connection).where(
                    Connection.status == ConnectionStatus.PENDING,
                    Connection.first_connected_at.is_(None),
                    Connection.created_at < cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    if not stale:
        return 0
    for connection in stale:
        await db.delete(connection)
    await db.commit()
    return len(stale)
