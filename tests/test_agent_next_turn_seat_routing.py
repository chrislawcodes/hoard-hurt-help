"""Fan-out tests: which seat/agent a next-turn request resolves to and serves.

Split from test_agent_next_turn_fanout.py — covers connection/agent/match
resolution, urgency ordering across servable turns, per-agent batching,
failover to a live connection, and the /api/agent/next-turns batch endpoint.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engine.model_provider_match import default_model_for_provider
from app.models.connection import ConnectionProvider, ConnectionStatus
from app.models.player import Player
from app.models.turn import TurnSubmission
from tests.agent_next_turn_fanout_support import (
    _create_match_with_turn,
    _seat_agent,
    app,
    engine,
    scoped_client,
    session_factory,
)
from tests.factories import make_connection, make_user

# `app` and `engine` are never named directly by a test here, but scoped_client
# (used by every test below) depends on the whole app -> session_factory ->
# engine fixture chain -- see the support module's docstring for why all four
# have to be imported together. Listing them in __all__ tells ruff they are
# used (the same pattern tests/conftest.py already uses for make_user).
__all__ = ["app", "engine", "scoped_client", "session_factory"]


async def test_one_connection_one_agent_one_match_returns_correct_version(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match, _turn = await _create_match_with_turn(db, "M_0001", deadline_seconds=60)
        agent, version, player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/{'Alpha'}",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="alpha strategy",
        )
        await db.commit()

    r = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "your_turn"
    assert body["match_id"] == "M_0001"
    assert body["agent_id"] == agent.id
    assert body["agent_name"] == "Alpha"
    # No preferred model set → payload carries the provider's default model
    # (the legacy AgentVersion.model is no longer forwarded).
    assert body["model"] == default_model_for_provider("claude")
    assert body["version_no"] == version.version_no
    assert body["seat_name"] == player.seat_name
    assert body["turn_token"] == body["current"]["turn_token"]
    assert body["agent_turn_token"] == f'{body["turn_token"]}:{agent.id}:M_0001'
    # One rulebook only: it rides inside base_prompt, not as its own key.
    assert "rules" not in body["static"]
    assert "base_prompt" in body["static"]
    assert f'as agent "{player.seat_name}"' in body["static"]["base_prompt"]
    assert "max 200 chars" in body["static"]["base_prompt"]
    assert "alpha strategy" not in body["static"]["base_prompt"]


async def test_multiple_agents_and_matches_pick_the_most_urgent_turn(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match_a, _turn_a = await _create_match_with_turn(db, "M_0100", deadline_seconds=120)
        match_b, _turn_b = await _create_match_with_turn(db, "M_0101", deadline_seconds=30)
        _agent_a, _version_a, _player_a = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match_a,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-haiku-4-5",
            strategy_text="alpha strategy",
        )
        agent_b, version_b, player_b = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match_b,
            seat_name=f"{user.handle}/Beta",
            agent_name="Beta",
            model="claude-opus-4-1",
            strategy_text="beta strategy",
        )
        await db.commit()

    r = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent_id"] == agent_b.id
    assert body["agent_name"] == "Beta"
    assert body["model"] == default_model_for_provider("claude")  # provider default
    assert body["version_no"] == version_b.version_no
    assert body["seat_name"] == player_b.seat_name
    assert body["match_id"] == match_b.id


async def test_same_match_agents_fetch_own_turn_and_wrong_agent_submit_is_rejected(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match, turn = await _create_match_with_turn(db, "M_0200", deadline_seconds=60)
        agent_a, _version_a, _player_a = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="alpha strategy",
        )
        agent_b, _version_b, _player_b = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Beta",
            agent_name="Beta",
            model="claude-haiku-4-5",
            strategy_text="beta strategy",
        )
        await db.commit()

    # Each agent fetching only its own turn is covered by the agent_id-filter
    # test; here the surviving contract is that a submit under the WRONG agent_id
    # for a claimed turn token is rejected without recording anything.
    next_turn = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert next_turn.status_code == 200, next_turn.text
    next_body = next_turn.json()

    wrong_agent_id = agent_b.id if next_body["agent_id"] == agent_a.id else agent_a.id
    wrong_submit = await scoped_client.post(
        f"/api/matches/{match.id}/submit",
        params={
            "agent_turn_token": next_body["agent_turn_token"],
            "agent_id": wrong_agent_id,
        },
        headers={"X-Connection-Key": key},
        json={
            "turn_token": next_body["turn_token"],
            "action": "HOARD",
            "target_id": None,
            "message": "hi",
            "thinking": "",
        },
    )
    assert wrong_submit.status_code == 409
    assert wrong_submit.json()["detail"]["error"]["code"] == "STALE_TURN_TOKEN"

    async with session_factory() as db:
        submissions = (
            await db.execute(
                select(TurnSubmission).where(TurnSubmission.turn_id == turn.id)
            )
        ).scalars().all()
        assert submissions == []

    correct_submit = await scoped_client.post(
        f"/api/matches/{match.id}/submit",
        params={
            "agent_turn_token": next_body["agent_turn_token"],
            "agent_id": next_body["agent_id"],
        },
        headers={"X-Connection-Key": key},
        json={
            "turn_token": next_body["turn_token"],
            "action": "HOARD",
            "target_id": None,
            "message": "hi",
            "thinking": "",
        },
    )
    assert correct_submit.status_code == 202, correct_submit.text


async def test_next_turn_agent_id_filter_and_batch_serve_each_agent(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Two agents share one connection AND one match. agent_id fetches just one;
    the batch returns both; the no-arg fetch still serves the most-urgent."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match, _turn = await _create_match_with_turn(db, "M_0201", deadline_seconds=60)
        agent_a, _version_a, player_a = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="alpha strategy",
        )
        agent_b, _version_b, player_b = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Beta",
            agent_name="Beta",
            model="claude-haiku-4-5",
            strategy_text="beta strategy",
        )
        await db.commit()

    # agent_id picks exactly that agent's turn — even though both share the match.
    only_a = await scoped_client.get(
        "/api/agent/next-turn",
        params={"agent_id": agent_a.id},
        headers={"X-Connection-Key": key},
    )
    assert only_a.status_code == 200, only_a.text
    body_a = only_a.json()
    assert body_a["status"] == "your_turn"
    assert body_a["agent_id"] == agent_a.id
    assert body_a["seat_name"] == player_a.seat_name
    # The static block's own identity field is projected from the served seat.
    assert body_a["static"]["your_agent_id"] == player_a.seat_name

    only_b = await scoped_client.get(
        "/api/agent/next-turn",
        params={"agent_id": agent_b.id},
        headers={"X-Connection-Key": key},
    )
    assert only_b.status_code == 200, only_b.text
    body_b = only_b.json()
    assert body_b["status"] == "your_turn"
    assert body_b["agent_id"] == agent_b.id
    assert body_b["seat_name"] == player_b.seat_name

    # The batch returns BOTH agents' turns, one entry per agent.
    batch = await scoped_client.get("/api/agent/next-turns", headers={"X-Connection-Key": key})
    assert batch.status_code == 200, batch.text
    batch_body = batch.json()
    assert batch_body["status"] == "your_turn"
    served_agent_ids = {turn["agent_id"] for turn in batch_body["turns"]}
    assert served_agent_ids == {agent_a.id, agent_b.id}

    # Regression: the no-arg fetch still serves a single most-urgent turn.
    any_turn = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert any_turn.status_code == 200, any_turn.text
    assert any_turn.json()["agent_id"] in {agent_a.id, agent_b.id}


async def test_paused_connection_next_turn_is_rejected(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        _connection, key = await make_connection(db, user, status=ConnectionStatus.PAUSED)
        await db.commit()

    r = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "CONNECTION_PAUSED"


async def test_urgency_ordering_prefers_the_earliest_deadline(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        late_match, _ = await _create_match_with_turn(db, "M_0300", deadline_seconds=90)
        early_match, _ = await _create_match_with_turn(db, "M_0301", deadline_seconds=15)
        _late_agent, _late_version, _late_player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=late_match,
            seat_name=f"{user.handle}/Late",
            agent_name="Late",
            model="claude-haiku-4-5",
            strategy_text="late strategy",
        )
        early_agent, early_version, early_player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=early_match,
            seat_name=f"{user.handle}/Early",
            agent_name="Early",
            model="claude-opus-4-1",
            strategy_text="early strategy",
        )
        await db.commit()

    r = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent_id"] == early_agent.id
    assert body["match_id"] == early_match.id
    assert body["model"] == default_model_for_provider("claude")  # provider default
    assert body["version_no"] == early_version.version_no
    assert body["seat_name"] == early_player.seat_name


async def test_failover_live_connection_serves_match_pinned_to_dead_connection(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        # Dead connection: stale last_seen, holds the pin.
        dead, _dead_key = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        dead.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=600)
        # Live connection covering the same provider.
        live, live_key = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        live.last_seen_at = datetime.now(timezone.utc)
        match, _turn = await _create_match_with_turn(db, "M_FAIL", deadline_seconds=60)
        _agent, _version, player = await _seat_agent(
            db,
            user=user,
            connection=dead,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="s",
        )
        # Pin the match to the now-dead connection.
        player.served_by_connection_id = dead.id
        player.served_pinned_at = datetime.now(timezone.utc) - timedelta(seconds=600)
        await db.commit()
        live_id = live.id
        player_id = player.id

    r = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": live_key})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "your_turn"
    # The pin moved to the live connection (failover).
    async with session_factory() as db:
        moved = (
            await db.execute(select(Player).where(Player.id == player_id))
        ).scalar_one()
        assert moved.served_by_connection_id == live_id


async def test_connection_only_serves_seats_for_its_own_ai(
    scoped_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matched routing: a seat joined with one AI is served only to a connection
    that covers that AI. A different-provider connection of the same user is
    handed nothing — so the seat plays as the AI the user picked, not whoever
    polls first."""
    # The non-matching connection has nothing to serve, so it long-polls; shrink
    # the hold so the test skips the full production wait. Routing assertions are
    # unchanged.
    monkeypatch.setattr("app.engine.agent_idle.LONG_POLL_HOLD_SECONDS", 0.4)
    monkeypatch.setattr(
        "app.engine.agent_play_next_turn.LONG_POLL_INTERVAL_SECONDS", 0.05
    )
    async with session_factory() as db:
        user = await make_user(db)
        _claude_conn, claude_key = await make_connection(
            db, user, provider=ConnectionProvider.CLAUDE
        )
        gemini_conn, gemini_key = await make_connection(
            db, user, provider=ConnectionProvider.GEMINI
        )
        match, _turn = await _create_match_with_turn(db, "M_MATCH", deadline_seconds=60)
        # Seat is joined with the Gemini connection → chosen_provider = "gemini".
        await _seat_agent(
            db,
            user=user,
            connection=gemini_conn,
            match=match,
            seat_name=f"{user.handle}/Gem",
            agent_name="Gem",
            model="gemini-3.1-flash-lite",
            strategy_text="s",
        )
        await db.commit()

    # The Claude connection covers only "claude" → it is NOT handed the gemini seat.
    r_claude = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": claude_key})
    assert r_claude.status_code == 200, r_claude.text
    assert r_claude.json()["status"] != "your_turn"

    # The Gemini connection covers "gemini" → it gets the turn, as Gemini.
    r_gemini = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": gemini_key})
    assert r_gemini.status_code == 200, r_gemini.text
    assert r_gemini.json()["status"] == "your_turn"
    assert r_gemini.json()["provider"] == "gemini"


