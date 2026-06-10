"""Connection pause/resume/delete and agent reattach actions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select, update

from app.deps import DbSession, require_user_with_handle
from app.engine.connection_health import LIVE_WINDOW_SECONDS
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.connection_setup import ConnectionSetup
from app.models.user import User

from app.routes.connections_setup import _load_owned_connection

router = APIRouter()


async def _provider_covered_by_other_live(
    db: DbSession,
    user_id: int,
    provider: ConnectionProvider,
    *,
    exclude_connection_id: int,
    now: datetime | None = None,
) -> bool:
    """True if ANOTHER live connection of this user still enables ``provider``.

    "Live" = not paused, not deleted, seen within LIVE_WINDOW_SECONDS. Used to
    decide whether disabling a provider (or deleting a machine) would strand the
    agents that depend on it.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=LIVE_WINDOW_SECONDS)
    count = await db.scalar(
        select(func.count())
        .select_from(ConnectionProviderRow)
        .join(Connection, Connection.id == ConnectionProviderRow.connection_id)
        .where(
            ConnectionProviderRow.provider == provider,
            ConnectionProviderRow.enabled.is_(True),
            Connection.user_id == user_id,
            Connection.id != exclude_connection_id,
            Connection.deleted_at.is_(None),
            Connection.status != ConnectionStatus.PAUSED,
            Connection.last_seen_at.is_not(None),
            Connection.last_seen_at >= cutoff,
        )
    )
    return bool(count)


async def _stranded_provider_agent_count(
    db: DbSession, user_id: int, provider: ConnectionProvider
) -> int:
    """How many of the user's active AI agents use ``provider``."""
    return (
        await db.scalar(
            select(func.count())
            .select_from(Agent)
            .where(
                Agent.user_id == user_id,
                Agent.kind == AgentKind.AI,
                Agent.status == AgentStatus.ACTIVE,
                Agent.archived_at.is_(None),
                Agent.provider == provider,
            )
        )
    ) or 0


@router.post("/{connection_id}/pause")
async def pause_connection(
    connection_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    connection = await _load_owned_connection(db, user, connection_id)
    connection.status = ConnectionStatus.PAUSED
    connection.paused_at = datetime.now(timezone.utc)
    connection.paused_reason = "owner"
    await db.commit()
    return RedirectResponse(
        url=f"/me/connections/{connection.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{connection_id}/resume")
async def resume_connection(
    connection_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    connection = await _load_owned_connection(db, user, connection_id)
    connection.status = ConnectionStatus.ACTIVE
    connection.paused_at = None
    connection.paused_reason = None
    await db.commit()
    return RedirectResponse(
        url=f"/me/connections/{connection.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{connection_id}/delete")
async def delete_connection(
    connection_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    connection = await _load_owned_connection(db, user, connection_id)
    # Deleting a connection must also stop the runner. Removing the connection
    # row marks it deleted, which makes the next runner check-in return a
    # dedicated shutdown response.
    now = datetime.now(timezone.utc)
    connection.deleted_at = now
    connection.status = ConnectionStatus.PAUSED
    connection.paused_at = now
    connection.paused_reason = "deleted"
    connection.runner_pid = None
    connection.prev_key_lookup = None
    # Coverage-aware delete: agents are no longer attached to a connection, so
    # deleting a machine just stops its runner and detaches any pending setup.
    # Agents stay ACTIVE — they keep playing on any OTHER live connection that
    # covers their provider; only agents now covered nowhere quietly wait.
    await db.execute(
        update(ConnectionSetup)
        .where(ConnectionSetup.connection_id == connection.id)
        .values(connection_id=None)
    )
    await db.commit()
    return RedirectResponse(url="/me/connections", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{connection_id}/providers/{provider}")
async def toggle_provider(
    connection_id: Annotated[int, Path()],
    provider: Annotated[ConnectionProvider, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    enabled: Annotated[bool, Query()] = True,
    confirm: Annotated[bool, Query()] = False,
) -> RedirectResponse:
    """Enable or disable a provider on this machine.

    Disabling a provider that would strand agents (no OTHER live connection
    covers it) requires ``confirm=true`` — the UI shows a warning first.
    """
    connection = await _load_owned_connection(db, user, connection_id)
    if not enabled and not confirm:
        covered = await _provider_covered_by_other_live(
            db, user.id, provider, exclude_connection_id=connection.id
        )
        stranded = await _stranded_provider_agent_count(db, user.id, provider)
        if not covered and stranded > 0:
            return RedirectResponse(
                url=(
                    f"/me/connections/{connection.id}"
                    f"?strand_provider={provider.value}&strand_count={stranded}"
                ),
                status_code=status.HTTP_303_SEE_OTHER,
            )
    row = (
        await db.execute(
            select(ConnectionProviderRow).where(
                ConnectionProviderRow.connection_id == connection.id,
                ConnectionProviderRow.provider == provider,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(
            ConnectionProviderRow(
                connection_id=connection.id,
                provider=provider,
                enabled=enabled,
                detected=False,
            )
        )
    else:
        row.enabled = enabled
    await db.commit()
    return RedirectResponse(
        url=f"/me/connections/{connection.id}", status_code=status.HTTP_303_SEE_OTHER
    )
