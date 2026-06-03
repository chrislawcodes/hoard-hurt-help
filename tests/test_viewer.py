"""Match viewer + SSE + spectator API tests."""

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models import (
    Base,
    Match,
    GameState,
    Player,
    StrategyPrompt,
    Turn,
    TurnMessage,
    TurnSubmission,
    User,
)
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
        g = Match(
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
            match_id="G_001",
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


async def _seed_two_phase_turn(
    reset_db,
    *,
    include_turn_messages: bool = True,
    talk_thinking: str = "private talk reasoning",
    act_thinking: str = "private act reasoning",
    talk_text: str = "public talk",
    legacy_message: str = "legacy public chat",
):
    async with reset_db() as db:
        player = (
            await db.execute(select(Player).where(Player.match_id == "G_001"))
        ).scalars().first()
        assert player is not None
        turn = Turn(
            match_id="G_001",
            round=1,
            turn=1,
            turn_token="tk1",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
            phase="act",
            resolved_at=datetime.now(timezone.utc),
        )
        db.add(turn)
        await db.flush()
        if include_turn_messages:
            db.add(
                TurnMessage(
                    turn_id=turn.id,
                    player_id=player.id,
                    text=talk_text,
                    thinking=talk_thinking,
                    was_defaulted=False,
                    submitted_at=datetime.now(timezone.utc),
                )
            )
        db.add(
            TurnSubmission(
                turn_id=turn.id,
                player_id=player.id,
                action="HOARD",
                message=legacy_message,
                thinking=act_thinking,
                points_delta=2,
                round_score_after=2,
                was_defaulted=False,
                submitted_at=datetime.now(timezone.utc),
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
async def test_viewer_renders_talk_then_act_and_thinking(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    await _seed_two_phase_turn(reset_db)
    r = await client.get("/games/G_001")
    assert r.status_code == 200
    assert "action-card hoard" in r.text
    assert "public talk" in r.text
    assert "Hoard" in r.text
    assert "+2" in r.text
    assert "private talk reasoning" in r.text
    assert "private act reasoning" in r.text
    # Thinking is shown to humans, paired with each move (no longer a closed toggle).
    assert 'class="thought"' in r.text


@pytest.mark.asyncio
async def test_legacy_viewer_falls_back_to_submission_message(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    await _seed_two_phase_turn(reset_db, include_turn_messages=False)
    r = await client.get("/games/G_001")
    assert r.status_code == 200
    assert "legacy public chat" in r.text


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
async def test_spectator_state_two_phase_shape_without_thinking(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    await _seed_two_phase_turn(reset_db)
    r = await client.get("/api/spectator/games/G_001/state")
    assert r.status_code == 200
    body = r.json()
    assert "thinking" not in r.text
    assert "private talk reasoning" not in r.text
    assert "private act reasoning" not in r.text
    assert body["history"] == [
        {
            "round": 1,
            "turn": 1,
            "messages": [
                {
                    "agent_id": "AI_0",
                    "message": "public talk",
                }
            ],
            "actions": [
                {
                    "agent_id": "AI_0",
                    "action": "HOARD",
                    "target_id": None,
                    "points_delta": 2,
                }
            ],
        }
    ]


@pytest.mark.asyncio
async def test_completed_viewer_has_round_nav(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    # A completed game needs at least one resolved turn for the round nav to show.
    async with reset_db() as db:
        from app.models import Player, Turn, TurnSubmission

        p = (await db.execute(__import__("sqlalchemy").select(Player))).scalars().first()
        t = Turn(
            match_id="G_001",
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
            match_id="G_001", user_id=u2.id, bot_id=bot2.id, agent_id="AI_1"
        )
        db.add(target)
        await db.flush()
        t = Turn(
            match_id="G_001",
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
    # The target and its loss are shown; the actor's own +0 is omitted because
    # the compact action line focuses on who the move lands on.
    assert "AI_1" in r.text
    assert "-4" in r.text
    assert "+0" not in r.text


@pytest.mark.asyncio
async def test_guide_serves_doc(client, reset_db):
    r = await client.get("/guide/setup-mcp")
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
