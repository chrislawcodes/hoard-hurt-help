"""Garbage collection for abandoned pending connection setups."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import cast

from sqlalchemy import delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connection import Connection, ConnectionStatus
from app.models.connection_setup import ConnectionSetup

_PENDING_MAX_AGE = timedelta(hours=24)


async def gc_pending_connections(
    db: AsyncSession, *, now: datetime | None = None
) -> int:
    """Delete stale pending setup drafts and legacy pending connections."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - _PENDING_MAX_AGE
    setup_count = 0
    try:
        setup_result = await db.execute(
            delete(ConnectionSetup).where(
                ConnectionSetup.completed_at.is_(None),
                ConnectionSetup.created_at < cutoff,
            )
        )
        setup_count = cast(CursorResult, setup_result).rowcount or 0
    except OperationalError:
        # Older deployments may still be running before the draft setup table
        # has been created. Keep the page working and let the legacy pending
        # connection cleanup continue to run.
        setup_count = 0
    connection_result = await db.execute(
        delete(Connection).where(
            Connection.status == ConnectionStatus.PENDING,
            Connection.first_connected_at.is_(None),
            Connection.created_at < cutoff,
        )
    )
    await db.commit()
    return setup_count + (cast(CursorResult, connection_result).rowcount or 0)
