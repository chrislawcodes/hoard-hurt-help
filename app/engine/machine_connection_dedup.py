"""Collapse a user's duplicate or abandoned machine connections.

A "machine connection" is one made from a connector setup key
(``mcp_connected_at IS NULL``) — the always-on connector or a paste-in loop.
Unlike MCP sign-in connections (one live row per provider, guarded by the
``uq_connections_mcp_user_provider_live`` unique index), machine rows have no
uniqueness: every fresh setup key that gets used mints another row. Re-running
the connector install, or a new browser session handing out a new key, therefore
piles up one card per key — often all the same laptop.

This module keeps the list honest by retiring the rows that no longer matter,
using the SAME reversible soft-delete the manual "Delete" button uses (see
``delete_connection``): mark ``deleted_at``, pause, and stop the runner. Nothing
is destroyed — a retired row still exists for history (a past turn's
``served_by_connection_id`` keeps resolving), and agents are never attached to a
connection (any live connection serves all of the user's agents), so retiring a
machine strands nothing as long as one live machine remains.

Two rules, both safe:

1. **Duplicate by name** — among a user's machine rows that share a computer name
   (hostname), keep the freshest-seen one and retire the rest.
2. **Abandoned** — retire a machine row not seen within ``STALE_AFTER`` and not
   brand-new, regardless of name. This clears the nameless graveyard that rule 1
   can't group (a paste-in loop that never reported a hostname).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.aware_datetime import ensure_aware
from app.models.connection import Connection, ConnectionStatus
from app.models.connection_setup import ConnectionSetup

# A machine seen this recently is treated as still in use; older than this with
# no fresher sibling, it is an abandoned row safe to retire. Generous on purpose
# so an intermittently-used machine is never retired out from under the user.
STALE_AFTER = timedelta(days=30)
# A never-used row younger than this is left alone — its setup may be mid-flight.
_NEW_GRACE = timedelta(hours=24)

RETIRE_REASON_DUPLICATE = "duplicate-machine"
RETIRE_REASON_STALE = "abandoned-machine"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _normalized_host(nickname: str | None) -> str | None:
    """A machine's identity for grouping: its trimmed, case-folded name."""
    if nickname is None:
        return None
    cleaned = nickname.strip().lower()
    return cleaned or None


def _freshness_key(connection: Connection) -> tuple[datetime, datetime, int]:
    """Sort key for "most alive": last seen, then created, then newest id."""
    seen = ensure_aware(connection.last_seen_at) if connection.last_seen_at else _EPOCH
    created = ensure_aware(connection.created_at) if connection.created_at else _EPOCH
    return (seen, created, connection.id)


def _is_abandoned(
    connection: Connection, *, stale_cutoff: datetime, new_cutoff: datetime
) -> bool:
    """True for a machine row old enough to retire on its own (rule 2)."""
    if connection.last_seen_at is not None:
        return ensure_aware(connection.last_seen_at) < stale_cutoff
    # Never used: keep it during the setup grace window, retire it after.
    return ensure_aware(connection.created_at) < new_cutoff


def _retire(connection: Connection, *, now: datetime, reason: str) -> None:
    """Reversible soft-delete, mirroring ``delete_connection``.

    The row stays in the table (history keeps resolving); it just leaves the
    list and the next runner check-in gets the shutdown response.
    """
    connection.deleted_at = now
    connection.status = ConnectionStatus.PAUSED
    connection.paused_at = now
    connection.paused_reason = reason
    connection.runner_pid = None
    connection.prev_key_lookup = None


async def dedupe_machine_connections(
    db: AsyncSession, user_id: int, *, now: datetime | None = None
) -> int:
    """Retire a user's duplicate / abandoned machine connections; return the count.

    Idempotent: a second call right after retires nothing. Safe to run on every
    connect-page load and on every machine reconnect — it only ever soft-deletes
    machine rows (``mcp_connected_at IS NULL``), never an MCP sign-in connection.
    """
    now = now or datetime.now(timezone.utc)
    stale_cutoff = now - STALE_AFTER
    new_cutoff = now - _NEW_GRACE

    rows = (
        (
            await db.execute(
                select(Connection).where(
                    Connection.user_id == user_id,
                    Connection.mcp_connected_at.is_(None),
                    Connection.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0

    # Rule 1: among same-named machines, every row but the freshest is a duplicate.
    groups: dict[str, list[Connection]] = {}
    for connection in rows:
        name = _normalized_host(connection.nickname)
        if name is not None:
            groups.setdefault(name, []).append(connection)
    duplicate_ids: set[int] = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        winner = max(group, key=_freshness_key)
        duplicate_ids.update(c.id for c in group if c.id != winner.id)

    retired_ids: list[int] = []
    for connection in rows:
        if connection.id in duplicate_ids:
            _retire(connection, now=now, reason=RETIRE_REASON_DUPLICATE)
            retired_ids.append(connection.id)
        elif _is_abandoned(connection, stale_cutoff=stale_cutoff, new_cutoff=new_cutoff):
            _retire(connection, now=now, reason=RETIRE_REASON_STALE)
            retired_ids.append(connection.id)

    if retired_ids:
        # Detach any pending setup that pointed at a retired row, mirroring the
        # manual delete so a stale setup can't keep a dead connection alive.
        await db.execute(
            update(ConnectionSetup)
            .where(ConnectionSetup.connection_id.in_(retired_ids))
            .values(connection_id=None)
        )
        await db.commit()
    return len(retired_ids)
