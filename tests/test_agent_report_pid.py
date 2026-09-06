"""Fan-out tests: the /api/agent/report-pid endpoint.

Split from test_agent_next_turn_fanout.py — covers detected-provider rows,
backward compatibility with an old connector that sends only {pid}, and the
hostname-default-vs-typed-name naming rule for an unnamed connection.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.connection import Connection, ConnectionProvider
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from tests.agent_next_turn_fanout_support import app, engine, scoped_client, session_factory
from tests.factories import make_connection, make_user

# `app` and `engine` are never named directly by a test here, but scoped_client
# (used by every test below) depends on the whole app -> session_factory ->
# engine fixture chain -- see the support module's docstring for why all four
# have to be imported together. Listing them in __all__ tells ruff they are
# used (the same pattern tests/conftest.py already uses for make_user).
__all__ = ["app", "engine", "scoped_client", "session_factory"]


async def test_report_pid_with_detected_providers_sets_detected_only(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        await db.commit()
        conn_id = connection.id

    r = await scoped_client.post(
        "/api/agent/report-pid",
        json={"pid": 4321, "detected_providers": ["claude", "openai"]},
        headers={"X-Connection-Key": key},
    )
    assert r.status_code == 204, r.text

    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(ConnectionProviderRow).where(
                        ConnectionProviderRow.connection_id == conn_id
                    )
                )
            )
            .scalars()
            .all()
        )
        by_provider = {row.provider.value: row for row in rows}
        # claude was the legacy enabled row: detected flips True, enabled untouched
        assert by_provider["claude"].detected is True
        assert by_provider["claude"].enabled is True
        # openai newly detected: detected True, enabled stays False (toggle is sacred)
        assert by_provider["openai"].detected is True
        assert by_provider["openai"].enabled is False
        conn = (
            await db.execute(select(Connection).where(Connection.id == conn_id))
        ).scalar_one()
        assert conn.runner_pid == 4321


async def test_report_pid_without_detected_providers_still_works(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """An OLD connector posts only {pid: ...}; it must not error (acceptance #7)."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        await db.commit()
        conn_id = connection.id

    r = await scoped_client.post(
        "/api/agent/report-pid", json={"pid": 99}, headers={"X-Connection-Key": key}
    )
    assert r.status_code == 204, r.text
    async with session_factory() as db:
        conn = (
            await db.execute(select(Connection).where(Connection.id == conn_id))
        ).scalar_one()
        assert conn.runner_pid == 99


async def test_report_pid_hostname_defaults_unnamed_connection(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """An unnamed machine takes the reported hostname as its default name."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user, nickname=None)
        await db.commit()
        conn_id = connection.id

    r = await scoped_client.post(
        "/api/agent/report-pid",
        json={"pid": 7, "hostname": "chris-macbook"},
        headers={"X-Connection-Key": key},
    )
    assert r.status_code == 204, r.text
    async with session_factory() as db:
        conn = (
            await db.execute(select(Connection).where(Connection.id == conn_id))
        ).scalar_one()
        assert conn.nickname == "chris-macbook"


async def test_report_pid_hostname_never_overrides_a_typed_name(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A name the operator typed always wins over the hostname default."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user, nickname="Battlestation")
        await db.commit()
        conn_id = connection.id

    r = await scoped_client.post(
        "/api/agent/report-pid",
        json={"pid": 7, "hostname": "chris-macbook"},
        headers={"X-Connection-Key": key},
    )
    assert r.status_code == 204, r.text
    async with session_factory() as db:
        conn = (
            await db.execute(select(Connection).where(Connection.id == conn_id))
        ).scalar_one()
        assert conn.nickname == "Battlestation"


