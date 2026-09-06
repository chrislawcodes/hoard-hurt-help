"""Fan-out tests: what a served next-turn payload contains.

Split from test_agent_next_turn_fanout.py — covers the provider field, history
windowing, pact values, coach notes, and the turn's static field block.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engine.model_provider_match import default_model_for_provider
from app.engine.tokens import generate_turn_token
from app.models.connection import ConnectionProvider
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission
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


async def test_next_turn_payload_includes_provider(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        match, _turn = await _create_match_with_turn(db, "M_PROV", deadline_seconds=60)
        await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="s",
        )
        await db.commit()

    r = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "claude"


async def test_provider_agnostic_serving_stamps_played_provider(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """An agent with no provider is served by ANY of the user's live connections,
    and the serving connection's provider is stamped onto the player as
    played_provider (the source of truth for the public 'played by' badge)."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(
            db, user, provider=ConnectionProvider.GEMINI
        )
        match, _turn = await _create_match_with_turn(db, "M_PA01", deadline_seconds=60)
        agent, version, player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Decoupled",
            agent_name="Decoupled",
            model="claude-sonnet-5",
            strategy_text="s",
        )
        # Decoupled agent: no stored provider, no stored model.
        agent.provider = None
        version.model = None
        await db.commit()
        player_id = player.id

    r = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "your_turn"
    assert body["agent_name"] == "Decoupled"
    # Payload provider reflects the serving connection, not the (absent) agent provider.
    assert body["provider"] == "gemini"
    # Decoupled agent (no preferred model) → the serving provider's default model.
    assert body["model"] == default_model_for_provider("gemini")

    async with session_factory() as db:
        refreshed = (
            await db.execute(select(Player).where(Player.id == player_id))
        ).scalar_one()
        assert refreshed.played_provider == "gemini"
        assert refreshed.served_by_connection_id == connection.id


async def test_next_turn_history_is_windowed_to_recent_turns(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The next-turn payload (the connector + MCP path) carries only the last
    couple of resolved turns, not the whole transcript — so a long mid-game match
    can't overflow a client's tool-output buffer and trip its loop detection."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        now = datetime.now(timezone.utc)
        match = Match(
            id="M_WIN",
            name="match-M_WIN",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=4,
        )
        db.add(match)
        await db.flush()
        _agent, _version, player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="s",
        )
        # Three resolved turns (1,1)..(1,3), then the open turn (1,4) the poll serves.
        for t in (1, 2, 3):
            resolved = Turn(
                match_id=match.id,
                round=1,
                turn=t,
                turn_token=generate_turn_token(),
                opened_at=now,
                deadline_at=now,
                resolved_at=now,
                phase="act",
            )
            db.add(resolved)
            await db.flush()
            db.add(
                TurnSubmission(
                    turn_id=resolved.id,
                    player_id=player.id,
                    action="HOARD",
                    target_player_id=None,
                    message=f"m{t}",
                    points_delta=2,
                    was_defaulted=False,
                )
            )
        db.add(
            Turn(
                match_id=match.id,
                round=1,
                turn=4,
                turn_token=generate_turn_token(),
                opened_at=now,
                deadline_at=now + timedelta(seconds=60),
                phase="act",
            )
        )
        await db.commit()

    r = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "your_turn"
    # Only the last two resolved turns ride along — (1,1) is dropped from the poll.
    assert [(t["round"], t["turn"]) for t in body["history"]] == [(1, 2), (1, 3)]


async def test_next_turn_payload_includes_current_pact_values(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """`your_private_state.pact_values` carries what a mutual HELP with each
    other seat would pay each side RIGHT NOW: decayed for a partner the agent
    already farmed once this match, fresh for one it never mutually helped
    (routed through `module.private_state_for`).

    Runs on the decay rule by name — a decayed-vs-fresh difference only exists
    there, and the flat rules would make both seats read the same number."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        now = datetime.now(timezone.utc)
        match = Match(
            id="M_PACT",
            name="match-M_PACT",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=2,
            mutual_help_mode="decay",
        )
        db.add(match)
        await db.flush()
        agent_a, _version_a, player_a = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="s",
        )
        _agent_b, _version_b, player_b = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Beta",
            agent_name="Beta",
            model="claude-haiku-4-5",
            strategy_text="s",
        )
        _agent_c, _version_c, player_c = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Gamma",
            agent_name="Gamma",
            model="claude-opus-4-1",
            strategy_text="s",
        )
        # Round 1, turn 1 (resolved): Alpha <-> Beta mutually helped once, so
        # their pair's k is now 1; Gamma stayed out of it (fresh pair with Alpha).
        resolved = Turn(
            match_id=match.id,
            round=1,
            turn=1,
            turn_token=generate_turn_token(),
            opened_at=now,
            deadline_at=now,
            resolved_at=now,
            phase="act",
        )
        db.add(resolved)
        await db.flush()
        db.add_all(
            [
                TurnSubmission(
                    turn_id=resolved.id,
                    player_id=player_a.id,
                    action="HELP",
                    target_player_id=player_b.id,
                    was_defaulted=False,
                ),
                TurnSubmission(
                    turn_id=resolved.id,
                    player_id=player_b.id,
                    action="HELP",
                    target_player_id=player_a.id,
                    was_defaulted=False,
                ),
                TurnSubmission(
                    turn_id=resolved.id,
                    player_id=player_c.id,
                    action="HOARD",
                    target_player_id=None,
                    was_defaulted=False,
                ),
            ]
        )
        # Round 1, turn 2: the open turn served next.
        db.add(
            Turn(
                match_id=match.id,
                round=1,
                turn=2,
                turn_token=generate_turn_token(),
                opened_at=now,
                deadline_at=now + timedelta(seconds=60),
                phase="act",
            )
        )
        await db.commit()

    # Next-turn fan-out path.
    r = await scoped_client.get(
        "/api/agent/next-turn",
        params={"agent_id": agent_a.id},
        headers={"X-Connection-Key": key},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "your_turn"
    pact_values = body["your_private_state"]["pact_values"]
    assert pact_values[player_b.seat_name] == 7  # farmed once already: 8 decays to 7
    assert pact_values[player_c.seat_name] == 8  # never mutually helped: fresh value
    assert "pact_values_note" in body["your_private_state"]


async def test_coach_note_served_on_turn_payload(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A coach note armed for the CURRENT round rides on the turn payload,
    gated to that round."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match, _turn = await _create_match_with_turn(db, "M_COACH", deadline_seconds=60)
        agent, _version, player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="alpha strategy",
        )
        player.coach_note = "Be cooperative this round"
        player.coach_note_round = match.current_round  # active NOW
        await db.commit()

    fanout = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert fanout.status_code == 200, fanout.text
    fanout_body = fanout.json()
    assert fanout_body["status"] == "your_turn"
    assert fanout_body["static"]["coach_note"] == "Be cooperative this round"


async def test_coach_note_for_a_future_round_is_absent_from_turn_payload(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The round gating holds: a note armed for a LATER round does not appear in
    the payload's static block."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match, _turn = await _create_match_with_turn(db, "M_COACH2", deadline_seconds=60)
        agent, _version, player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="alpha strategy",
        )
        player.coach_note = "Armed for a later round"
        player.coach_note_round = match.current_round + 1
        await db.commit()

    fanout = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert fanout.status_code == 200, fanout.text
    assert "coach_note" not in fanout.json()["static"]


async def test_turn_static_block_carries_unified_fields(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The next-turn static block carries the full identity/rules field set built
    by build_turn_static_dict — including the conditional coach_note — not an
    empty shell."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match, _turn = await _create_match_with_turn(db, "M_DRIFT", deadline_seconds=60)
        agent, _version, player = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="alpha strategy",
        )
        player.coach_note = "Watch the leader"
        player.coach_note_round = match.current_round
        await db.commit()

    fanout = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert fanout.status_code == 200, fanout.text

    static = fanout.json()["static"]
    for field in ("match_id", "game", "base_prompt", "coach_note"):
        assert field in static
    # One id per block: `game_id` was `match_id` restated, and nothing read it.
    assert "game_id" not in static


