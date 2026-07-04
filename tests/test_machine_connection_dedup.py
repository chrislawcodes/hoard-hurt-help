"""Tests for collapsing duplicate / abandoned machine connections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.engine.machine_connection_dedup import (
    RETIRE_REASON_DUPLICATE,
    RETIRE_REASON_STALE,
    dedupe_machine_connections,
)
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_connection_key
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_setup import ConnectionSetup
from tests.factories import make_user

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)


async def _machine(
    db,
    user,
    *,
    nickname: str | None = None,
    last_seen: datetime | None = None,
    created: datetime | None = None,
    mcp: bool = False,
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
) -> Connection:
    """Create one connection with full control of the fields dedupe reads."""
    key = generate_connection_key()
    connection = Connection(
        user_id=user.id,
        provider=ConnectionProvider.CLAUDE,
        nickname=nickname,
        key_lookup=bot_key_lookup(key),
        key_hint=bot_key_hint(key),
        status=status,
        last_seen_at=last_seen,
        created_at=created or NOW,
        # An MCP sign-in connection has this set; a machine connection leaves it NULL.
        mcp_connected_at=NOW if mcp else None,
    )
    db.add(connection)
    await db.flush()
    return connection


async def _reload(db, connection: Connection) -> Connection:
    return (
        await db.execute(select(Connection).where(Connection.id == connection.id))
    ).scalar_one()


async def test_keeps_freshest_of_same_named_machines(db):
    user = await make_user(db)
    stale = await _machine(db, user, nickname="macbook", last_seen=NOW - timedelta(hours=2))
    fresh = await _machine(db, user, nickname="macbook", last_seen=NOW - timedelta(minutes=1))

    retired = await dedupe_machine_connections(db, user.id, now=NOW)

    assert retired == 1
    assert (await _reload(db, fresh)).deleted_at is None
    older = await _reload(db, stale)
    assert older.deleted_at is not None
    assert older.paused_reason == RETIRE_REASON_DUPLICATE
    assert older.status == ConnectionStatus.PAUSED
    assert older.runner_pid is None


async def test_hostname_grouping_ignores_case_and_whitespace(db):
    user = await make_user(db)
    a = await _machine(db, user, nickname="MacBook ", last_seen=NOW - timedelta(hours=3))
    b = await _machine(db, user, nickname=" macbook", last_seen=NOW - timedelta(minutes=5))

    retired = await dedupe_machine_connections(db, user.id, now=NOW)

    assert retired == 1
    assert (await _reload(db, b)).deleted_at is None
    assert (await _reload(db, a)).deleted_at is not None


async def test_retires_abandoned_nameless_machine(db):
    user = await make_user(db)
    abandoned = await _machine(db, user, nickname=None, last_seen=NOW - timedelta(days=40))

    retired = await dedupe_machine_connections(db, user.id, now=NOW)

    assert retired == 1
    row = await _reload(db, abandoned)
    assert row.deleted_at is not None
    assert row.paused_reason == RETIRE_REASON_STALE


async def test_keeps_recently_seen_nameless_machine(db):
    user = await make_user(db)
    recent = await _machine(db, user, nickname=None, last_seen=NOW - timedelta(days=1))

    retired = await dedupe_machine_connections(db, user.id, now=NOW)

    assert retired == 0
    assert (await _reload(db, recent)).deleted_at is None


async def test_keeps_brand_new_never_used_machine(db):
    user = await make_user(db)
    # Created moments ago, never made a call yet — mid-setup, must not be retired.
    fresh = await _machine(
        db, user, nickname=None, last_seen=None, created=NOW - timedelta(hours=1)
    )

    retired = await dedupe_machine_connections(db, user.id, now=NOW)

    assert retired == 0
    assert (await _reload(db, fresh)).deleted_at is None


async def test_retires_old_never_used_machine(db):
    user = await make_user(db)
    stale = await _machine(
        db, user, nickname=None, last_seen=None, created=NOW - timedelta(days=2)
    )

    retired = await dedupe_machine_connections(db, user.id, now=NOW)

    assert retired == 1
    assert (await _reload(db, stale)).paused_reason == RETIRE_REASON_STALE


async def test_never_touches_mcp_connections(db):
    user = await make_user(db)
    # An MCP sign-in connection, long idle — never a candidate for retiring here.
    mcp = await _machine(
        db, user, nickname="claude", last_seen=NOW - timedelta(days=90), mcp=True
    )

    retired = await dedupe_machine_connections(db, user.id, now=NOW)

    assert retired == 0
    assert (await _reload(db, mcp)).deleted_at is None


async def test_isolated_per_user(db):
    owner = await make_user(db, 1)
    other = await make_user(db, 2)
    owner_old = await _machine(db, owner, nickname="box", last_seen=NOW - timedelta(hours=2))
    owner_new = await _machine(db, owner, nickname="box", last_seen=NOW - timedelta(minutes=1))
    other_old = await _machine(db, other, nickname="box", last_seen=NOW - timedelta(hours=2))
    other_new = await _machine(db, other, nickname="box", last_seen=NOW - timedelta(minutes=1))

    retired = await dedupe_machine_connections(db, owner.id, now=NOW)

    assert retired == 1
    assert (await _reload(db, owner_old)).deleted_at is not None
    assert (await _reload(db, owner_new)).deleted_at is None
    # The other user's rows are left completely alone.
    assert (await _reload(db, other_old)).deleted_at is None
    assert (await _reload(db, other_new)).deleted_at is None


async def test_idempotent(db):
    user = await make_user(db)
    await _machine(db, user, nickname="macbook", last_seen=NOW - timedelta(hours=2))
    await _machine(db, user, nickname="macbook", last_seen=NOW - timedelta(minutes=1))

    first = await dedupe_machine_connections(db, user.id, now=NOW)
    second = await dedupe_machine_connections(db, user.id, now=NOW)

    assert first == 1
    assert second == 0


async def test_detaches_pending_setup_from_retired_row(db):
    user = await make_user(db)
    old = await _machine(db, user, nickname="macbook", last_seen=NOW - timedelta(hours=2))
    await _machine(db, user, nickname="macbook", last_seen=NOW - timedelta(minutes=1))
    key = generate_connection_key()
    setup = ConnectionSetup(
        user_id=user.id,
        key_lookup=bot_key_lookup(key),
        key_hint=bot_key_hint(key),
        connection_id=old.id,
        completed_at=NOW - timedelta(hours=2),
    )
    db.add(setup)
    await db.flush()

    await dedupe_machine_connections(db, user.id, now=NOW)

    refreshed = (
        await db.execute(select(ConnectionSetup).where(ConnectionSetup.id == setup.id))
    ).scalar_one()
    assert refreshed.connection_id is None
