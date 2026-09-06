"""Fan-out tests: the /api/agent/next-turns batch endpoint.

Split from test_agent_next_turn_fanout.py (further split out of the seat-
routing group, which was over the file-size cap) — the plural endpoint that
returns every servable turn at once, across mixed phases and already-
submitted turns.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.match import GameState, Match
from app.models.turn import TurnMessage, TurnSubmission
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


async def test_next_turns_returns_every_servable_turn_at_once(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The batch endpoint hands back ALL open turns across the connection's
    matches in one poll, so the runner can drive them concurrently. The singular
    endpoint, by contrast, returns only the most urgent one.
    """
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match_a, _turn_a = await _create_match_with_turn(db, "M_0701", deadline_seconds=60)
        match_b, _turn_b = await _create_match_with_turn(db, "M_0702", deadline_seconds=30)
        await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match_a,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="alpha strategy",
        )
        await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match_b,
            seat_name=f"{user.handle}/Beta",
            agent_name="Beta",
            model="claude-haiku-4-5",
            strategy_text="beta strategy",
        )
        await db.commit()

    # Singular endpoint: only the most urgent (M_0702, nearer deadline).
    single = await scoped_client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert single.status_code == 200, single.text
    assert single.json()["match_id"] == "M_0702"

    # Batch endpoint: BOTH matches in one response.
    batch = await scoped_client.get("/api/agent/next-turns", headers={"X-Connection-Key": key})
    assert batch.status_code == 200, batch.text
    body = batch.json()
    assert body["status"] == "your_turn"
    match_ids = sorted(t["match_id"] for t in body["turns"])
    assert match_ids == ["M_0701", "M_0702"]
    # Each turn carries its own binding token so workers submit independently.
    assert all(t["agent_turn_token"] for t in body["turns"])
    assert len({t["agent_turn_token"] for t in body["turns"]}) == 2


async def test_next_turns_omits_a_turn_already_submitted(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A turn the agent has already moved on drops out of the batch, so a worker
    isn't re-dispatched for work that's done."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        match_a, _turn_a = await _create_match_with_turn(db, "M_0711", deadline_seconds=60)
        match_b, turn_b = await _create_match_with_turn(db, "M_0712", deadline_seconds=60)
        await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match_a,
            seat_name=f"{user.handle}/Alpha",
            agent_name="Alpha",
            model="claude-sonnet-5",
            strategy_text="alpha strategy",
        )
        _agent_b, _version_b, player_b = await _seat_agent(
            db,
            user=user,
            connection=connection,
            match=match_b,
            seat_name=f"{user.handle}/Beta",
            agent_name="Beta",
            model="claude-haiku-4-5",
            strategy_text="beta strategy",
        )
        # Beta has already submitted a real (non-defaulted) move for its turn.
        db.add(
            TurnSubmission(
                turn_id=turn_b.id,
                player_id=player_b.id,
                action="HOARD",
                target_player_id=None,
                was_defaulted=False,
            )
        )
        await db.commit()

    batch = await scoped_client.get("/api/agent/next-turns", headers={"X-Connection-Key": key})
    assert batch.status_code == 200, batch.text
    body = batch.json()
    assert [t["match_id"] for t in body["turns"]] == ["M_0711"]


async def test_filter_to_candidates_batches_mixed_phase_seats(
    scoped_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The batched owes-a-move filter behaves per seat exactly like the old
    per-seat queries across a mixed board: an act turn already submitted is
    skipped, a talk turn already messaged is skipped, a talk turn not yet
    messaged is served, a seat whose only submission was defaulted is served,
    and a seat with no open turn at all serves nothing."""
    async with session_factory() as db:
        user = await make_user(db)
        connection, key = await make_connection(db, user)
        # A: act phase, real submission -> skipped.
        match_a, turn_a = await _create_match_with_turn(db, "M_FA", deadline_seconds=60)
        # B: talk phase, real talk message -> skipped.
        match_b, turn_b = await _create_match_with_turn(
            db, "M_FB", deadline_seconds=60, phase="talk"
        )
        # C: talk phase, no message yet -> served.
        match_c, _turn_c = await _create_match_with_turn(
            db, "M_FC", deadline_seconds=60, phase="talk"
        )
        # D: act phase, only a DEFAULTED submission -> still owed, served.
        match_d, turn_d = await _create_match_with_turn(db, "M_FD", deadline_seconds=60)
        # E: active match with NO open turn -> nothing to serve.
        now = datetime.now(timezone.utc)
        match_e = Match(
            id="M_FE",
            name="match-M_FE",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=1),
            started_at=now - timedelta(minutes=1),
            per_turn_deadline_seconds=60,
            current_round=1,
            current_turn=1,
        )
        db.add(match_e)
        await db.flush()

        players = {}
        for label, match in (
            ("A", match_a),
            ("B", match_b),
            ("C", match_c),
            ("D", match_d),
            ("E", match_e),
        ):
            _agent, _version, player = await _seat_agent(
                db,
                user=user,
                connection=connection,
                match=match,
                seat_name=f"{user.handle}/{label}",
                agent_name=label,
                model="claude-sonnet-5",
                strategy_text="s",
            )
            players[label] = player
        db.add(
            TurnSubmission(
                turn_id=turn_a.id,
                player_id=players["A"].id,
                action="HOARD",
                target_player_id=None,
                was_defaulted=False,
            )
        )
        db.add(
            TurnMessage(
                turn_id=turn_b.id,
                player_id=players["B"].id,
                text="already talked",
                was_defaulted=False,
                submitted_at=now,
            )
        )
        db.add(
            TurnSubmission(
                turn_id=turn_d.id,
                player_id=players["D"].id,
                action="HOARD",
                target_player_id=None,
                was_defaulted=True,
            )
        )
        await db.commit()

    batch = await scoped_client.get("/api/agent/next-turns", headers={"X-Connection-Key": key})
    assert batch.status_code == 200, batch.text
    body = batch.json()
    assert body["status"] == "your_turn"
    assert sorted(t["match_id"] for t in body["turns"]) == ["M_FC", "M_FD"]


