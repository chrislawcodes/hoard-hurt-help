"""Agent API HTTP tests — join, poll, submit, leave."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.models import Base, Game, GameState, Player, Turn, TurnSubmission
from app.engine.tokens import generate_turn_token
from tests.factories import seat_player


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
    monkeypatch.setattr("app.routes.agent_api._last_poll", {})
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
        g = Game(
            id="G_001",
            name="t",
            state=state,
            scheduled_start=scheduled_start
            or datetime.now(timezone.utc) + timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add(g)
        await db.flush()
        players = []
        for i in range(n_players):
            p = await seat_player(db, g.id, f"AI_{i}", i=i)
            players.append(p)
        await db.commit()
        return g.id, players


# Joining a game is now a web action (POST /games/{id}/join with a bot_id) — see
# tests/test_lobby.py. The agent API is play-only, so the old API /join tests are gone.


@pytest.mark.asyncio
async def test_poll_invalid_key(client, reset_db):
    await _seed_game(reset_db, state=GameState.ACTIVE)
    r = await client.get(
        "/api/games/G_001/turn",
        headers={"X-Agent-Key": "sk_game_bogus"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "INVALID_KEY"


@pytest.mark.asyncio
async def test_poll_game_not_started(client, reset_db):
    # Scheduled an hour out → far from start → slow poll cadence.
    _, players = await _seed_game(reset_db, state=GameState.REGISTERING, n_players=1)
    r = await client.get(
        "/api/games/G_001/turn",
        headers={"X-Agent-Key": players[0]._test_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "waiting"
    assert body["reason"] == "game_not_started"
    assert body["next_poll_after_seconds"] == 30


@pytest.mark.asyncio
async def test_poll_not_started_near_start_polls_faster(client, reset_db):
    # Within 3 minutes of start → tighten the poll cadence.
    soon = datetime.now(timezone.utc) + timedelta(seconds=90)
    _, players = await _seed_game(
        reset_db, state=GameState.REGISTERING, n_players=1, scheduled_start=soon
    )
    r = await client.get(
        "/api/games/G_001/turn",
        headers={"X-Agent-Key": players[0]._test_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["reason"] == "game_not_started"
    assert body["next_poll_after_seconds"] == 5


@pytest.mark.asyncio
async def test_poll_active_no_open_turn_cadence(client, reset_db):
    # Live game with no open turn → "active" waiting cadence.
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=1)
    r = await client.get(
        "/api/games/G_001/turn",
        headers={"X-Agent-Key": players[0]._test_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "waiting"
    assert body["reason"] == "turn_not_open"
    assert body["next_poll_after_seconds"] == 5


@pytest.mark.asyncio
async def test_poll_your_turn_then_submit(client, reset_db):
    """Open a turn manually, poll → your_turn → submit → 202."""
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    p0 = players[0]
    key = p0._test_key

    # Open a turn in the DB.
    async with reset_db() as db:
        from sqlalchemy import select

        game = (await db.execute(select(Game).where(Game.id == "G_001"))).scalar_one()
        game.current_round = 1
        game.current_turn = 1
        now = datetime.now(timezone.utc)
        t = Turn(
            game_id=game.id,
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

    r = await client.get("/api/games/G_001/turn", headers={"X-Agent-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "your_turn"
    # Raw payload: static + full append-only history + scoreboard + current.
    # No pre-digested `summary`.
    assert "summary" not in body
    assert body["current"]["turn_token"] == turn_token
    assert isinstance(body["history"], list)
    assert isinstance(body["scoreboard"], list)
    assert "rules" in body["static"]
    turn_token = body["current"]["turn_token"]

    # Submit Hoard.
    r2 = await client.post(
        "/api/games/G_001/submit",
        headers={"X-Agent-Key": key},
        json={"turn_token": turn_token, "action": "HOARD", "target_id": None, "message": "hi"},
    )
    assert r2.status_code == 202, r2.text

    # Idempotent re-submit returns same status.
    r3 = await client.post(
        "/api/games/G_001/submit",
        headers={"X-Agent-Key": key},
        json={"turn_token": turn_token, "action": "HOARD", "target_id": None, "message": "hi"},
    )
    assert r3.status_code == 202


@pytest.mark.asyncio
async def test_submit_invalid_target(client, reset_db):
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    p0 = players[0]
    key = p0._test_key

    async with reset_db() as db:
        from sqlalchemy import select

        game = (await db.execute(select(Game).where(Game.id == "G_001"))).scalar_one()
        now = datetime.now(timezone.utc)
        t = Turn(
            game_id=game.id,
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
        headers={"X-Agent-Key": key},
        json={
            "turn_token": turn_token,
            "action": "HELP",
            "target_id": p0.agent_id,
            "message": "",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "INVALID_TARGET"


@pytest.mark.asyncio
async def test_rate_limit(client, reset_db):
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=1)
    key = players[0]._test_key
    # First poll OK.
    r1 = await client.get("/api/games/G_001/turn", headers={"X-Agent-Key": key})
    assert r1.status_code == 200
    # Immediate second poll → 429.
    r2 = await client.get("/api/games/G_001/turn", headers={"X-Agent-Key": key})
    assert r2.status_code == 429
    assert r2.json()["detail"]["error"]["code"] == "RATE_LIMITED"


# --- Pull-on-demand detail endpoints (feature 002, US3) ---


async def _seed_resolved_turn(reset_db, game_id, rnd, turn, subs):
    """subs: list of (player_id, action, target_player_id|None, message, pts, score)."""
    from sqlalchemy import select

    async with reset_db() as db:
        (await db.execute(select(Game).where(Game.id == game_id))).scalar_one()
        now = datetime.now(timezone.utc)
        t = Turn(
            game_id=game_id,
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


@pytest.mark.asyncio
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
        "/api/games/G_001/history/opponents/AI_1", headers={"X-Agent-Key": p0._test_key}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["opponent_id"] == "AI_1"
    assert len(body["turns"]) == 1
    actors = {a["agent_id"] for a in body["turns"][0]["actions"]}
    assert actors == {"AI_0", "AI_1"}  # AI_2's hoard is not part of this pair


@pytest.mark.asyncio
async def test_pull_opponent_history_unknown(client, reset_db):
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    r = await client.get(
        "/api/games/G_001/history/opponents/NOPE", headers={"X-Agent-Key": players[0]._test_key}
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "INVALID_TARGET"


@pytest.mark.asyncio
async def test_pull_chat_since_cursor(client, reset_db):
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    p0, p1 = players
    await _seed_resolved_turn(reset_db, "G_001", 1, 1, [(p0.id, "HOARD", None, "turn one", 2, 2)])
    await _seed_resolved_turn(reset_db, "G_001", 1, 2, [(p1.id, "HOARD", None, "turn two", 2, 2)])
    r = await client.get(
        "/api/games/G_001/chat", params={"since": "1.1"}, headers={"X-Agent-Key": p0._test_key}
    )
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    assert [m["message"] for m in msgs] == ["turn two"]
    assert r.json()["next_cursor"] == "1.2"


@pytest.mark.asyncio
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
    r = await client.get("/api/games/G_001/turns/1/1", headers={"X-Agent-Key": p0._test_key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["round"] == 1 and body["turn"] == 1
    assert len(body["actions"]) == 2


@pytest.mark.asyncio
async def test_pull_turn_detail_missing(client, reset_db):
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    r = await client.get("/api/games/G_001/turns/9/9", headers={"X-Agent-Key": players[0]._test_key})
    assert r.status_code == 404
    assert r.json()["detail"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_pull_standings(client, reset_db):
    from sqlalchemy import select

    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=3)
    async with reset_db() as db:
        ps = (await db.execute(select(Player).where(Player.game_id == "G_001"))).scalars().all()
        for p in ps:
            p.current_round_score = {"AI_0": 5, "AI_1": 9, "AI_2": 1}[p.agent_id]
        await db.commit()
    r = await client.get("/api/games/G_001/standings", headers={"X-Agent-Key": players[0]._test_key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows"][0]["agent_id"] == "AI_1"  # highest round score → rank 1
    assert body["total_players"] == 3


@pytest.mark.asyncio
async def test_pull_rate_limited(client, reset_db):
    _, players = await _seed_game(reset_db, state=GameState.ACTIVE, n_players=2)
    key = players[0]._test_key
    r1 = await client.get("/api/games/G_001/standings", headers={"X-Agent-Key": key})
    assert r1.status_code == 200
    r2 = await client.get("/api/games/G_001/standings", headers={"X-Agent-Key": key})
    assert r2.status_code == 429
    assert r2.json()["detail"]["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
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
        game = (await db.execute(select(Game).where(Game.id == "G_001"))).scalar_one()
        game.current_round, game.current_turn = 1, 2
        now = datetime.now(timezone.utc)
        db.add(
            Turn(
                game_id="G_001",
                round=1,
                turn=2,
                turn_token=generate_turn_token(),
                opened_at=now,
                deadline_at=now + timedelta(seconds=60),
            )
        )
        await db.commit()

    r = await client.get("/api/games/G_001/turn", headers={"X-Agent-Key": p0._test_key})
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
