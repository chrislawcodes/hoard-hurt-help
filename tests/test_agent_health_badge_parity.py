"""Pins the agent list and agent detail pages to agree on the health badge.

Before this fix, `app/routes/agents_list.py` treated `CONNECTED_NOT_LIVE` as
"Ready" while `app/routes/agents_detail.py` treated the same rung as
"No live connection" (DISCONNECTED) — the same agent could show contradictory
badges depending which page you were on. Both routes now build their badge via
the single `readiness_health_status` mapping in
`app/routes/agents_health_presenter.py`, so this test drives both real HTTP
routes for a CONNECTED_NOT_LIVE user/agent and asserts they render the same
badge label.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.connection import ConnectionProvider
from tests.conftest import signed_in_cookies as _cookies
from tests.factories import make_agent, make_connection, make_user


def _mcp_recent() -> datetime:
    """mcp_connected_at 5 days ago — inside the 90-day MCP validity window."""
    return datetime.now(timezone.utc) - timedelta(days=5)


async def test_list_and_detail_agree_on_connected_not_live_badge(
    client: AsyncClient,
    reset_db: async_sessionmaker,
) -> None:
    """A connection that is set up (recent mcp_connected_at) but has not been
    seen live recently resolves to ProviderReadiness.CONNECTED_NOT_LIVE. The
    agent list page and the agent detail page must show the same badge for it.
    """
    async with reset_db() as db:
        user = await make_user(db, i=900, handle="paritycheck")
        connection, _key = await make_connection(
            db,
            user,
            provider=ConnectionProvider.CLAUDE,
            mcp_connected_at=_mcp_recent(),
            last_seen_at=None,  # not seen live right now → CONNECTED_NOT_LIVE
        )
        agent, _version = await make_agent(
            db, user, connection=connection, name="Parity"
        )
        await db.commit()

    cookies = _cookies(user.id)

    list_resp = await client.get("/me/agents", cookies=cookies)
    detail_resp = await client.get(f"/me/agents/{agent.id}", cookies=cookies)

    assert list_resp.status_code == 200
    assert detail_resp.status_code == 200

    # Both pages must show the same "no live connection" badge for this
    # agent — not "Ready" on one and "No live connection" on the other.
    assert "No live connection" in list_resp.text
    assert "No live connection" in detail_resp.text

    # The bug this pins: the list page used to render a "badge-ok"/"Ready"
    # badge for this exact readiness rung. Confirm that regression stays fixed.
    assert "badge-ok" not in list_resp.text
