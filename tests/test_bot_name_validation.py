"""Agent names may contain spaces and run up to 120 characters.

The friendly agent label on the /me/agents page is separate from the in-game
seat name (a stricter 32-char, no-space field set at game entry). This pins the
agent label's rules so a future tweak to the in-game validator can't quietly
tighten them.
"""

from datetime import datetime, timezone

from httpx import AsyncClient
from sqlalchemy import select

from app.models import Agent
from app.models.connection import ConnectionProvider
from tests.factories import make_connection, make_user
from tests.conftest import session_cookie as _cookie


async def test_long_name_with_spaces_is_accepted(client: AsyncClient, reset_db) -> None:
    # Spaces, mixed case, right at the 120-char ceiling.
    name = ("Strategic Tit For Tat " * 6).strip()  # 137 -> trimmed below
    name = name[:120].strip()
    assert " " in name and 100 < len(name) <= 120

    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        connection.mcp_connected_at = datetime.now(timezone.utc)  # set up (MCP-recent)
        await db.commit()

    # Set on the client (not per-request): a per-request cookies= kwarg does not
    # reliably survive httpx's own redirect-following, and this POST redirects.
    client.cookies.set("hhh_session", _cookie(user.id))
    r = await client.post(
        "/me/agents/new",
        data={"name": name},
        follow_redirects=True,
    )
    # Accepted (not a 400) — post-create lands on the lobby; the agent then
    # shows on the /me/agents list with the exact name we asked for.
    assert r.status_code == 200, r.text
    agents_page = await client.get("/me/agents")
    assert name in agents_page.text


async def test_name_over_120_chars_is_rejected(client: AsyncClient, reset_db) -> None:
    # The name column is VARCHAR(120). Postgres rejects anything longer, so the
    # form must catch it with a friendly 400 rather than 500 in prod.
    name = "x" * 121

    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        connection.mcp_connected_at = datetime.now(timezone.utc)  # set up (MCP-recent)
        await db.commit()

    client.cookies.set("hhh_session", _cookie(user.id))
    r = await client.post(
        "/me/agents/new",
        data={"name": name, "model": "claude-haiku-4-5"},
        follow_redirects=False,
    )

    assert r.status_code == 400, r.text
    # Nothing was persisted.
    async with reset_db() as db:
        from sqlalchemy import select as _select

        from app.models.agent import Agent

        assert (await db.execute(_select(Agent))).scalars().all() == []


async def test_rename_to_long_spaced_name_is_accepted(client: AsyncClient, reset_db) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        connection.mcp_connected_at = datetime.now(timezone.utc)  # set up (MCP-recent)
        await db.commit()

    client.cookies.set("hhh_session", _cookie(user.id))
    created = await client.post("/me/agents/new", data={"name": "Atlas"}, follow_redirects=True)
    assert created.status_code == 200, created.text
    # Create now lands on the lobby, so look the new agent's id up directly.
    async with reset_db() as db:
        agent_id = (
            await db.execute(
                select(Agent.id).where(Agent.user_id == user.id, Agent.name == "Atlas")
            )
        ).scalar_one()
    new_name = "Atlas The Diplomatic Cooperator Agent"
    renamed = await client.post(
        f"/me/agents/{agent_id}/rename",
        data={"name": new_name},
        follow_redirects=True,
    )

    assert renamed.status_code == 200, renamed.text
    assert new_name in renamed.text
