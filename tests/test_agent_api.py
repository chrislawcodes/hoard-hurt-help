"""Agent API HTTP tests — join, poll, submit, leave."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.models import Base, Game, GameState, Player, Turn, User
from app.engine.tokens import generate_turn_token


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
) -> tuple[str, list[Player]]:
    async with reset_db() as db:
        g = Game(
            id="G_001",
            name="t",
            state=state,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add(g)
        await db.flush()
        players = []
        for i in range(n_players):
            u = User(google_sub=f"sub-{i}", email=f"u{i}@t.com")
            db.add(u)
            await db.flush()
            from app.engine.tokens import generate_agent_key, hash_agent_key

            key = generate_agent_key()
            p = Player(
                game_id=g.id,
                user_id=u.id,
                agent_id=f"AI_{i}",
                agent_key_hash=hash_agent_key(key),
            )
            p._test_key = key  # type: ignore[attr-defined]
            db.add(p)
            await db.flush()
            players.append(p)
        await db.commit()
        return g.id, players


@pytest.mark.asyncio
async def test_join_happy_path(client, reset_db):
    await _seed_game(reset_db, state=GameState.REGISTERING)
    r = await client.post(
        "/api/games/G_001/join",
        json={"display_name": "AI_qa", "strategy_prompt": "play fairly"},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["agent_key"].startswith("sk_game_")
    assert data["agent_id"] == "AI_qa"


@pytest.mark.asyncio
async def test_join_game_not_found(client, reset_db):
    r = await client.post(
        "/api/games/G_999/join",
        json={"display_name": "AI_x", "strategy_prompt": "x"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"]["code"] == "GAME_NOT_FOUND"


@pytest.mark.asyncio
async def test_join_duplicate_name(client, reset_db):
    await _seed_game(reset_db, state=GameState.REGISTERING, n_players=1)  # AI_0 exists
    r = await client.post(
        "/api/games/G_001/join",
        json={"display_name": "AI_0", "strategy_prompt": "x"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "INVALID_DISPLAY_NAME"


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
    _, players = await _seed_game(reset_db, state=GameState.REGISTERING, n_players=1)
    r = await client.get(
        "/api/games/G_001/turn",
        headers={"X-Agent-Key": players[0]._test_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "waiting"
    assert body["reason"] == "game_not_started"


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
        )
        db.add(t)
        await db.commit()
        await db.refresh(t)
        turn_token = t.turn_token

    r = await client.get("/api/games/G_001/turn", headers={"X-Agent-Key": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "your_turn"
    assert body["dynamic"]["turn_token"] == turn_token

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
