"""Agent API HTTP tests — join, poll, submit, leave."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.models import Base, Match, GameState, Player, Turn, TurnSubmission
from app.engine.tokens import generate_turn_token
from tests.factories import make_match, seat_player


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    """Rebind the production SessionLocal/engine to an in-memory SQLite per test."""
    from app.db import make_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)
    # The deps.get_db reads via the imported SessionLocal symbol, which we just patched.
    monkeypatch.setattr("app.routes.agent_api._last_pull", {})

    yield test_factory

    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_game(
    reset_db: async_sessionmaker,
    state: GameState = GameState.REGISTERING,
    n_players: int = 0,
    scheduled_start: datetime | None = None,
) -> tuple[str, list[Player]]:
    async with reset_db() as db:
        g = await make_match(
            db, "G_001", state=state, name="t", scheduled_start=scheduled_start
        )
        players = []
        for i in range(n_players):
            p = await seat_player(db, g.id, f"AI_{i}", i=i)
            players.append(p)
        await db.commit()
        return g.id, players


# Joining a game is now a web action (POST /games/{id}/join with a bot_id) — see
# tests/test_lobby.py. The agent API is play-only, so the old API /join tests are gone.


async def test_next_turn_invalid_key(client, reset_db):
    await _seed_game(reset_db, state=GameState.ACTIVE)
    r = await client.get(
        "/api/agent/next-turn",
        headers={"X-Connection-Key": "sk_game_bogus"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "INVALID_KEY"


async def test_next_turn_your_turn_then_submit(client, reset_db):
    """Poll the next-turn loop → your_turn → submit → 202, and an idempotent
    re-submit → 202 again."""
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    key = players[0]._test_key

    # Open a turn in the DB.
    async with reset_db() as db:
        from sqlalchemy import select

        game = (await db.execute(select(Match).where(Match.id == "G_001"))).scalar_one()
        game.current_round = 1
        game.current_turn = 1
        now = datetime.now(timezone.utc)
        t = Turn(
            match_id=game.id,
            round=1,
            turn=1,
            turn_token=generate_turn_token(),
            opened_at=now,
            deadline_at=now + timedelta(seconds=60),
            phase="act",
        )
        db.add(t)
        await db.commit()

    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "your_turn"
    # Raw payload: static + append-only history + scoreboard + current.
    # No pre-digested `summary`.
    assert "summary" not in body
    assert isinstance(body["history"], list)
    assert isinstance(body["scoreboard"], list)
    assert "rules" in body["static"]
    turn_token = body["current"]["turn_token"]
    # The loop hands back the (agent, match)-bound submit token directly.
    agent_turn_token = body["agent_turn_token"]

    # Submit Hoard.
    r2 = await client.post(
        "/api/games/G_001/submit",
        params={"agent_turn_token": agent_turn_token},
        headers={"X-Connection-Key": key},
        json={"turn_token": turn_token, "action": "HOARD", "target_id": None, "message": "hi"},
    )
    assert r2.status_code == 202, r2.text

    # Idempotent re-submit returns same status.
    r3 = await client.post(
        "/api/games/G_001/submit",
        params={"agent_turn_token": agent_turn_token},
        headers={"X-Connection-Key": key},
        json={"turn_token": turn_token, "action": "HOARD", "target_id": None, "message": "hi"},
    )
    assert r3.status_code == 202


async def test_submit_invalid_target(client, reset_db):
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    p0 = players[0]
    key = p0._test_key

    async with reset_db() as db:
        from sqlalchemy import select

        game = (await db.execute(select(Match).where(Match.id == "G_001"))).scalar_one()
        now = datetime.now(timezone.utc)
        t = Turn(
            match_id=game.id,
            round=1,
            turn=1,
            turn_token=generate_turn_token(),
            opened_at=now,
            deadline_at=now + timedelta(seconds=60),
            phase="act",
        )
        db.add(t)
        await db.commit()
        await db.refresh(t)
        turn_token = t.turn_token

    # Self-target HELP.
    r = await client.post(
        "/api/games/G_001/submit",
        params={"agent_turn_token": f"{turn_token}:{p0.agent_id}:G_001"},
        headers={"X-Connection-Key": key},
        json={
            "turn_token": turn_token,
            "action": "HELP",
            "target_id": p0.seat_name,
            "message": "",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "INVALID_TARGET"


async def _open_act_turn(reset_db, match_id: str = "G_001") -> str:
    """Open an act-phase turn in the DB and return its turn_token."""
    from sqlalchemy import select

    async with reset_db() as db:
        game = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()
        game.current_round = 1
        game.current_turn = 1
        now = datetime.now(timezone.utc)
        t = Turn(
            match_id=game.id,
            round=1,
            turn=1,
            turn_token=generate_turn_token(),
            opened_at=now,
            deadline_at=now + timedelta(seconds=60),
            phase="act",
        )
        db.add(t)
        await db.commit()
        await db.refresh(t)
        return t.turn_token


async def _submit_help(client, key, turn_token, agent_id, target_id):
    return await client.post(
        "/api/games/G_001/submit",
        params={"agent_turn_token": f"{turn_token}:{agent_id}:G_001"},
        headers={"X-Connection-Key": key},
        json={
            "turn_token": turn_token,
            "action": "HELP",
            "target_id": target_id,
            "message": "",
        },
    )


async def test_submit_target_case_insensitive(client, reset_db):
    """A HELP that names a real player with different casing still resolves (202)."""
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    p0, p1 = players[0], players[1]
    turn_token = await _open_act_turn(reset_db)
    r = await _submit_help(client, p0._test_key, turn_token, p0.agent_id, p1.seat_name.lower())
    assert r.status_code == 202, r.text


async def test_submit_target_whitespace_trimmed(client, reset_db):
    """A HELP target with stray surrounding whitespace still resolves (202)."""
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    p0, p1 = players[0], players[1]
    turn_token = await _open_act_turn(reset_db)
    r = await _submit_help(client, p0._test_key, turn_token, p0.agent_id, f"  {p1.seat_name}  ")
    assert r.status_code == 202, r.text


async def test_submit_unknown_target_still_rejected(client, reset_db):
    """A target that matches no player is still a 400 — the guard isn't loosened."""
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    p0 = players[0]
    turn_token = await _open_act_turn(reset_db)
    r = await _submit_help(client, p0._test_key, turn_token, p0.agent_id, "NoSuchAgent")
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "INVALID_TARGET"


async def test_submit_self_target_case_variant_still_rejected(client, reset_db):
    """A case-variant self-target resolves to self and is still rejected (400)."""
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    p0 = players[0]
    turn_token = await _open_act_turn(reset_db)
    r = await _submit_help(client, p0._test_key, turn_token, p0.agent_id, p0.seat_name.lower())
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "INVALID_TARGET"


# --- Pull-on-demand detail endpoints (feature 002, US3) ---


async def _seed_resolved_turn(reset_db, match_id, rnd, turn, subs):
    """subs: list of (player_id, action, target_player_id|None, message, pts, score)."""
    from sqlalchemy import select

    async with reset_db() as db:
        (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()
        now = datetime.now(timezone.utc)
        t = Turn(
            match_id=match_id,
            round=rnd,
            turn=turn,
            turn_token=generate_turn_token(),
            opened_at=now,
            deadline_at=now,
            resolved_at=now,
        )
        db.add(t)
        await db.flush()
        for pid, action, target, msg, pts, score in subs:
            db.add(
                TurnSubmission(
                    turn_id=t.id,
                    player_id=pid,
                    action=action,
                    target_player_id=target,
                    message=msg,
                    points_delta=pts,
                    round_score_after=score,
                    was_defaulted=False,
                    submitted_at=now,
                )
            )
        await db.commit()


async def test_pull_opponent_history(client, reset_db):
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=3)
    p0, p1, p2 = players
    await _seed_resolved_turn(
        reset_db,
        "G_001",
        1,
        1,
        [
            (p0.id, "HELP", p1.id, "hi", 0, 0),
            (p1.id, "HURT", p0.id, "take that", 0, 0),
            (p2.id, "HOARD", None, "", 2, 2),
        ],
    )
    r = await client.get(
        f"/api/games/G_001/history/opponents/{p1.seat_name}",
        headers={"X-Connection-Key": p0._test_key},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["opponent_id"] == p1.seat_name
    assert len(body["turns"]) == 1
    actors = {a["agent_id"] for a in body["turns"][0]["actions"]}
    assert actors == {p0.seat_name, p1.seat_name}  # AI_2's hoard is not part of this pair


async def test_pull_opponent_history_unknown(client, reset_db):
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    r = await client.get(
        "/api/games/G_001/history/opponents/NOPE", headers={"X-Connection-Key": players[0]._test_key}
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "INVALID_TARGET"


async def test_pull_chat_since_cursor(client, reset_db):
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    p0, p1 = players
    await _seed_resolved_turn(reset_db, "G_001", 1, 1, [(p0.id, "HOARD", None, "turn one", 2, 2)])
    await _seed_resolved_turn(reset_db, "G_001", 1, 2, [(p1.id, "HOARD", None, "turn two", 2, 2)])
    r = await client.get(
        "/api/games/G_001/chat", params={"since": "1.1"}, headers={"X-Connection-Key": p0._test_key}
    )
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    assert [m["message"] for m in msgs] == ["turn two"]
    assert r.json()["next_cursor"] == "1.2"


async def test_pull_turn_detail(client, reset_db):
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    p0, p1 = players
    await _seed_resolved_turn(
        reset_db,
        "G_001",
        1,
        1,
        [(p0.id, "HOARD", None, "a", 2, 2), (p1.id, "HELP", p0.id, "b", 0, 0)],
    )
    r = await client.get("/api/games/G_001/turns/1/1", headers={"X-Connection-Key": p0._test_key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["round"] == 1 and body["turn"] == 1
    assert len(body["actions"]) == 2


async def test_pull_turn_detail_missing(client, reset_db):
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    r = await client.get("/api/games/G_001/turns/9/9", headers={"X-Connection-Key": players[0]._test_key})
    assert r.status_code == 404
    assert r.json()["detail"]["error"]["code"] == "NOT_FOUND"


async def test_pull_standings(client, reset_db):
    from sqlalchemy import select

    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=3)
    async with reset_db() as db:
        ps = (await db.execute(select(Player).where(Player.match_id == "G_001"))).scalars().all()
        for p in ps:
            p.current_round_score = {"AI_0": 5, "AI_1": 9, "AI_2": 1}[p.seat_name]
        await db.commit()
    r = await client.get("/api/games/G_001/standings", headers={"X-Connection-Key": players[0]._test_key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows"][0]["agent_id"] == "AI_1"  # highest round score → rank 1
    assert body["total_players"] == 3


async def test_pull_rate_limited(client, reset_db):
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    key = players[0]._test_key
    r1 = await client.get("/api/games/G_001/standings", headers={"X-Connection-Key": key})
    assert r1.status_code == 200
    r2 = await client.get("/api/games/G_001/standings", headers={"X-Connection-Key": key})
    assert r2.status_code == 429
    assert r2.json()["detail"]["error"]["code"] == "RATE_LIMITED"


async def test_directed_message_appears_next_turn(client, reset_db):
    """A move + message from last turn shows up in the raw history the bot reads."""
    from sqlalchemy import select

    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    p0, p1 = players
    # Turn 1 resolves: AI_1 hurts AI_0 with a pointed message.
    await _seed_resolved_turn(
        reset_db,
        "G_001",
        1,
        1,
        [
            (p0.id, "HOARD", None, "", 2, 2),
            (p1.id, "HURT", p0.id, "stop hoarding or I keep hitting you", 0, 0),
        ],
    )
    # Open turn 2.
    async with reset_db() as db:
        game = (await db.execute(select(Match).where(Match.id == "G_001"))).scalar_one()
        game.current_round, game.current_turn = 1, 2
        now = datetime.now(timezone.utc)
        db.add(
            Turn(
                match_id="G_001",
                round=1,
                turn=2,
                turn_token=generate_turn_token(),
                opened_at=now,
                deadline_at=now + timedelta(seconds=60),
            )
        )
        await db.commit()

    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": p0._test_key})
    assert r.status_code == 200, r.text
    body = r.json()
    # Turn 1 is in the raw history the bot reads itself — AI_1's hurt-with-message
    # on AI_0 is right there, no pre-digestion.
    turn1 = next(t for t in body["history"] if t["round"] == 1 and t["turn"] == 1)
    hit = next(a for a in turn1["actions"] if a["agent_id"] == "AI_1")
    assert hit["action"] == "HURT"
    assert hit["target_id"] == "AI_0"
    assert "stop hoarding" in hit["message"]
    # Current scores are present for the bot to read.
    assert any(s["agent_id"] == "AI_0" for s in body["scoreboard"])


async def test_load_public_action_records_windows_to_recent_turns(reset_db):
    """The read helper loads only the last N resolved turns when windowed, and the
    whole transcript when not — the single knob the lean poll payload turns on."""
    from sqlalchemy import select

    from app.engine.agent_play_reads import _load_public_action_records

    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    p0, _p1 = players
    for t in range(1, 5):  # four resolved turns: (1,1)..(1,4)
        await _seed_resolved_turn(
            reset_db, "G_001", 1, t, [(p0.id, "HOARD", None, f"m{t}", 2, 2 * t)]
        )

    async with reset_db() as db:
        ps = (
            (await db.execute(select(Player).where(Player.match_id == "G_001")))
            .scalars()
            .all()
        )
        windowed = await _load_public_action_records(db, "G_001", ps, recent_turns=2)
        full = await _load_public_action_records(db, "G_001", ps)

    # Windowed → only the two most-recent resolved turns, oldest-to-newest.
    assert [(r.round, r.turn) for r in windowed] == [(1, 3), (1, 4)]
    # Unwindowed (default) → the whole transcript, unchanged.
    assert [(r.round, r.turn) for r in full] == [(1, 1), (1, 2), (1, 3), (1, 4)]


async def test_chat_returns_whole_unwindowed_transcript(client, reset_db):
    """The turn payload's history is windowed to recent turns (covered on the
    next-turn path and by test_load_public_action_records_windows_to_recent_turns);
    the on-demand chat is the catch-up channel that returns the WHOLE transcript."""
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    p0, _p1 = players
    for t in range(1, 5):  # resolved turns (1,1)..(1,4)
        await _seed_resolved_turn(
            reset_db, "G_001", 1, t, [(p0.id, "HOARD", None, f"m{t}", 2, 2 * t)]
        )

    # Catch-up channel: chat returns every turn's message, unwindowed (no cursor).
    chat = await client.get("/api/games/G_001/chat", headers={"X-Connection-Key": p0._test_key})
    assert chat.status_code == 200, chat.text
    assert [m["message"] for m in chat.json()["messages"]] == ["m1", "m2", "m3", "m4"]
