"""Slice 4b: per-model verification status on the agent detail page + the FR-014
join-time warning when a preferred model is verified-failing."""

from __future__ import annotations

from datetime import datetime, timezone

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engine.model_verification import record_results
from app.models.connection import ConnectionProvider
from app.models.match import GameState, Match
from tests.conftest import session_cookie
from tests.factories import make_agent, make_connection, make_user

GAME = "hoard-hurt-help"


async def _seed(reset_db: async_sessionmaker, *, outcome: str) -> tuple[int, int]:
    """A user with a live Claude connection and an agent whose preferred model has
    the given verification outcome."""
    async with reset_db() as db:
        user = await make_user(db, 0)
        conn, _ = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        now = datetime.now(timezone.utc)
        conn.mcp_connected_at = now
        conn.last_seen_at = now
        conn.last_polled_at = now
        agent, _ = await make_agent(db, user, connection=conn, name="opus-agent")
        agent.preferred_model = "claude-opus-4-8"
        await db.flush()
        await record_results(
            db,
            conn,
            [{"provider": "claude", "model": "claude-opus-4-8",
              "outcome": outcome, "error_text": "no access to model"}],
        )
        await db.commit()
        return user.id, agent.id


async def test_detail_shows_failed_badge(client: AsyncClient, reset_db: async_sessionmaker) -> None:
    uid, aid = await _seed(reset_db, outcome="failed")
    r = await client.get(f"/me/agents/{aid}", cookies={"hhh_session": session_cookie(uid)})
    assert r.status_code == 200
    assert "can't run" in r.text


async def test_detail_shows_verified_badge(client: AsyncClient, reset_db: async_sessionmaker) -> None:
    uid, aid = await _seed(reset_db, outcome="verified")
    r = await client.get(f"/me/agents/{aid}", cookies={"hhh_session": session_cookie(uid)})
    assert r.status_code == 200
    assert "your connector can run this model" in r.text


async def test_join_warns_on_failing_preferred_model(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    uid, _aid = await _seed(reset_db, outcome="failed")
    async with reset_db() as db:
        db.add(
            Match(
                id="M_0001",
                name="Match",
                game=GAME,
                state=GameState.REGISTERING,
                scheduled_start=datetime.now(timezone.utc),
                per_turn_deadline_seconds=60,
                max_players=20,
            )
        )
        await db.commit()
    r = await client.get(
        f"/games/{GAME}/matches/M_0001/join",
        cookies={"hhh_session": session_cookie(uid)},
    )
    assert r.status_code == 200
    assert "preferred model can't run" in r.text
