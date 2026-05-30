"""Game viewer + SSE + spectator API tests."""

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models import Base, Game, GameState, Player, StrategyPrompt, User
from tests.factories import make_bot


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


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed(reset_db, state=GameState.ACTIVE):
    async with reset_db() as db:
        u = User(google_sub="u", email="u@t.com")
        db.add(u)
        await db.flush()
        g = Game(
            id="G_001",
            name="Test",
            state=state,
            scheduled_start=datetime.now(timezone.utc),
            current_round=1,
            current_turn=1,
        )
        db.add(g)
        await db.flush()
        bot, _ = await make_bot(db, u, name="AI_0")
        p = Player(
            game_id="G_001",
            user_id=u.id,
            bot_id=bot.id,
            agent_id="AI_0",
        )
        db.add(p)
        await db.flush()
        db.add(
            StrategyPrompt(
                player_id=p.id,
                prompt_text="SECRET STRATEGY DO NOT LEAK",
                is_default=False,
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_viewer_renders_active(client, reset_db):
    await _seed(reset_db, GameState.ACTIVE)
    r = await client.get("/games/G_001")
    assert r.status_code == 200
    assert "Test" in r.text


@pytest.mark.asyncio
async def test_viewer_does_not_leak_strategy(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    r = await client.get("/games/G_001")
    assert r.status_code == 200
    assert "SECRET STRATEGY" not in r.text


@pytest.mark.asyncio
async def test_spectator_state_no_prompts(client, reset_db):
    await _seed(reset_db, GameState.ACTIVE)
    r = await client.get("/api/spectator/games/G_001/state")
    assert r.status_code == 200
    body = r.json()
    # Schema has no strategy field; verify by absence.
    assert "strategy_prompt" not in r.text
    assert body["name"] == "Test"


@pytest.mark.asyncio
async def test_completed_viewer_has_round_nav(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    # A completed game needs at least one resolved turn for the round nav to show.
    async with reset_db() as db:
        from app.models import Player, Turn, TurnSubmission

        p = (await db.execute(__import__("sqlalchemy").select(Player))).scalars().first()
        t = Turn(
            game_id="G_001",
            round=1,
            turn=1,
            turn_token="tk1",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
            resolved_at=datetime.now(timezone.utc),
        )
        db.add(t)
        await db.flush()
        db.add(
            TurnSubmission(
                turn_id=t.id,
                player_id=p.id,
                action="HOARD",
                message="hi",
                points_delta=2,
                round_score_after=2,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
    r = await client.get("/games/G_001")
    assert r.status_code == 200
    # Round-jump bar and grouped round section are present.
    assert "round-nav" in r.text
    assert 'data-round="1"' in r.text
    assert "round-section" in r.text


@pytest.mark.asyncio
async def test_viewer_shows_per_move_effect_on_target(client, reset_db):
    """A HURT row must show the loss on the TARGET, not just the actor's +0."""
    await _seed(reset_db, GameState.COMPLETED)
    async with reset_db() as db:
        import sqlalchemy

        from app.models import Player, Turn, TurnSubmission, User

        actor = (await db.execute(sqlalchemy.select(Player))).scalars().first()
        # Second player to be the HURT target.
        u2 = User(google_sub="u2", email="u2@t.com")
        db.add(u2)
        await db.flush()
        bot2, _ = await make_bot(db, u2, name="AI_1")
        target = Player(
            game_id="G_001", user_id=u2.id, bot_id=bot2.id, agent_id="AI_1"
        )
        db.add(target)
        await db.flush()
        t = Turn(
            game_id="G_001",
            round=1,
            turn=1,
            turn_token="tk1",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
            resolved_at=datetime.now(timezone.utc),
        )
        db.add(t)
        await db.flush()
        # Actor HURTs the target. Actor's own net is 0; the -4 lands on the target.
        db.add(
            TurnSubmission(
                turn_id=t.id,
                player_id=actor.id,
                action="HURT",
                target_player_id=target.id,
                message="take that",
                points_delta=0,
                round_score_after=0,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    r = await client.get("/games/G_001")
    assert r.status_code == 200
    # The target and its loss are shown; the actor's own +0 is shown too.
    assert "AI_1" in r.text
    assert "-4" in r.text
    assert "+0" in r.text


@pytest.mark.asyncio
async def test_guide_serves_doc(client, reset_db):
    r = await client.get("/guide/setup-claude")
    assert r.status_code == 200
    assert "claude mcp add" in r.text


@pytest.mark.asyncio
async def test_guide_rejects_unknown_and_traversal(client, reset_db):
    assert (await client.get("/guide/nonexistent")).status_code == 404
    assert (await client.get("/guide/..%2f..%2fetc%2fpasswd")).status_code == 404


@pytest.mark.asyncio
async def test_list_games_public(client, reset_db):
    """GET /api/games returns a JSON list of all games."""
    await _seed(reset_db, GameState.ACTIVE)
    r = await client.get("/api/games")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["id"] == "G_001"
    assert body[0]["state"] == "active"
    assert body[0]["player_count"] == 1
    assert "strategy_prompt" not in r.text  # no leak


@pytest.mark.asyncio
async def test_list_games_public_filter_by_state(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    r = await client.get("/api/games?state=active")
    assert r.status_code == 200
    assert r.json() == []
    r2 = await client.get("/api/games?state=completed")
    assert len(r2.json()) == 1
