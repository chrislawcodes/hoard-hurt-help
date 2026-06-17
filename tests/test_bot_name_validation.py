"""Agent names may contain spaces and run up to 120 characters.

The friendly agent label on the /me/agents page is separate from the in-game
seat name (a stricter 32-char, no-space field set at game entry). This pins the
agent label's rules so a future tweak to the in-game validator can't quietly
tighten them.
"""

import base64
import json
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner

from app.config import settings
from app.main import app
from app.models import Base
from app.models.connection import ConnectionProvider
from tests.factories import make_connection, make_user


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)
    yield test_factory
    await test_engine.dispose()


def _cookie(user_id: int) -> str:
    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return signer.sign(payload).decode()


def _authed_client(user_id: int, *, follow_redirects: bool = True) -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"hhh_session": _cookie(user_id)},
        follow_redirects=follow_redirects,
    )


@pytest.mark.asyncio
async def test_long_name_with_spaces_is_accepted(reset_db) -> None:
    # Spaces, mixed case, right at the 120-char ceiling.
    name = ("Strategic Tit For Tat " * 6).strip()  # 137 -> trimmed below
    name = name[:120].strip()
    assert " " in name and 100 < len(name) <= 120

    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        connection.mcp_connected_at = datetime.now(timezone.utc)  # set up (MCP-recent)
        await db.commit()

    async with _authed_client(user.id) as c:
        r = await c.post(
            "/me/agents/new",
            data={"name": name, "model": "claude-haiku-4-5"},
        )

    # Lands on the new agent's detail page (200 after the redirect is followed),
    # and the page shows the name we asked for.
    assert r.status_code == 200, r.text
    assert name in r.text


@pytest.mark.asyncio
async def test_name_over_120_chars_is_rejected(reset_db) -> None:
    # The name column is VARCHAR(120). Postgres rejects anything longer, so the
    # form must catch it with a friendly 400 rather than 500 in prod.
    name = "x" * 121

    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        connection.mcp_connected_at = datetime.now(timezone.utc)  # set up (MCP-recent)
        await db.commit()

    async with _authed_client(user.id, follow_redirects=False) as c:
        r = await c.post(
            "/me/agents/new",
            data={"name": name, "model": "claude-haiku-4-5"},
        )

    assert r.status_code == 400, r.text
    # Nothing was persisted.
    async with reset_db() as db:
        from sqlalchemy import select as _select

        from app.models.agent import Agent

        assert (await db.execute(_select(Agent))).scalars().all() == []


@pytest.mark.asyncio
async def test_rename_to_long_spaced_name_is_accepted(reset_db) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        connection.mcp_connected_at = datetime.now(timezone.utc)  # set up (MCP-recent)
        await db.commit()

    async with _authed_client(user.id) as c:
        created = await c.post(
            "/me/agents/new",
            data={
                "name": "Atlas",
                "model": "claude-haiku-4-5",
            },
        )
        assert created.status_code == 200, created.text
        # The detail URL carries the new agent's id; rename through it.
        agent_id = created.url.path.rsplit("/", 1)[-1]
        new_name = "Atlas The Diplomatic Cooperator Agent"
        renamed = await c.post(f"/me/agents/{agent_id}/rename", data={"name": new_name})

    assert renamed.status_code == 200, renamed.text
    assert new_name in renamed.text
