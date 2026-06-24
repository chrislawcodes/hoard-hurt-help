"""Connection-scoped next-turn endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import DbSession, require_connection
from app.engine.agent_play import get_next_turn, get_next_turns
from app.engine.machine_connection_dedup import dedupe_machine_connections
from app.models.connection import Connection, ConnectionProvider
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/next-turn", response_model=None)
async def next_turn(
    connection: Annotated[Connection, Depends(require_connection)],
    db: DbSession,
    agent_id: int | None = None,
) -> dict[str, object]:
    # Pacing (long-poll hold + wait number) is decided server-side, off the
    # caller's soonest game — the client no longer asks for a hold length.
    return await get_next_turn(db, connection, agent_id=agent_id)


@router.get("/next-turns", response_model=None)
async def next_turns(
    connection: Annotated[Connection, Depends(require_connection)],
    db: DbSession,
) -> dict[str, object]:
    return await get_next_turns(db, connection)


class _ReportPidRequest(BaseModel):
    pid: int
    detected_providers: list[str] | None = None
    hostname: str | None = None


async def _apply_detected_providers(
    db: AsyncSession, connection: Connection, detected: list[str]
) -> None:
    """Update connection_providers.detected from the connector's CLI sweep."""
    detected_values = {provider.strip() for provider in detected if provider.strip()}
    rows = (
        (
            await db.execute(
                select(ConnectionProviderRow).where(
                    ConnectionProviderRow.connection_id == connection.id
                )
            )
        )
        .scalars()
        .all()
    )
    seen: set[str] = set()
    for row in rows:
        is_detected = row.provider.value in detected_values
        row.detected = is_detected
        row.detected_detail = "CLI detected" if is_detected else "not found"
        seen.add(row.provider.value)
    for value in detected_values - seen:
        try:
            provider = ConnectionProvider(value)
        except ValueError:
            continue
        db.add(
            ConnectionProviderRow(
                connection_id=connection.id,
                provider=provider,
                enabled=False,
                detected=True,
                detected_detail="CLI detected",
            )
        )


@router.post("/report-pid", status_code=204)
async def report_pid(
    body: _ReportPidRequest,
    connection: Annotated[Connection, Depends(require_connection)],
    db: DbSession,
) -> None:
    connection.runner_pid = body.pid
    if body.detected_providers is not None:
        await _apply_detected_providers(db, connection, body.detected_providers)
    if connection.nickname is None and body.hostname and body.hostname.strip():
        connection.nickname = body.hostname.strip()[:60]
    await db.commit()
    # Now that this machine has reported its name, fold any older row for the same
    # laptop (a stale key the user re-ran setup with) into this live one.
    await dedupe_machine_connections(db, connection.user_id)
