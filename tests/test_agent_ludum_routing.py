"""Routing tests for the Agent Ludum front page + the platform/game URL split.

`/` now serves the Agent Ludum marketing page; the Hoard·Hurt·Help lobby moved
to `/play/hoard-hurt-help`; the per-match viewer at `/games/{id}` is unchanged.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.models import Base, Game, GameState


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    from app.db import make_engine

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


async def _seed_game(
    reset_db: async_sessionmaker,
    game_id: str = "G_001",
    name: str = "Test Game",
    state: GameState = GameState.REGISTERING,
) -> Game:
    async with reset_db() as db:
        g = Game(
            id=game_id,
            name=name,
            state=state,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add(g)
        await db.commit()
        await db.refresh(g)
        return g


@pytest.mark.asyncio
async def test_root_serves_agent_ludum_marketing(client, reset_db):
    """`/` is the Agent Ludum platform page with a CTA into the HHH lobby."""
    r = await client.get("/")
    assert r.status_code == 200
    assert "Agent" in r.text and "Ludum" in r.text
    # Stable brand descriptor (title + subhead + footer) — a durable marker of the
    # marketing page that doesn't couple the test to the churnable hero headline.
    assert "Multiplayer games for AI agents" in r.text
    # The funnel: a primary CTA points at the game lobby, not at `/`.
    assert 'href="/play/hoard-hurt-help"' in r.text


@pytest.mark.asyncio
async def test_lobby_served_at_play_path(client, reset_db):
    """The HHH lobby (upcoming games etc.) now lives at /play/hoard-hurt-help."""
    await _seed_game(reset_db)
    r = await client.get("/play/hoard-hurt-help")
    assert r.status_code == 200
    assert "Test Game" in r.text  # the upcoming-games listing renders here


@pytest.mark.asyncio
async def test_game_viewer_unchanged(client, reset_db):
    """The per-match viewer pattern /games/{id} is untouched by the split."""
    await _seed_game(reset_db, game_id="G_view", state=GameState.ACTIVE)
    r = await client.get("/games/G_view")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_active_game_viewer_wires_live_sse(client, reset_db):
    """An active game exposes the SSE stream + live-fragment URLs the page's
    plain-JS EventSource needs, and must NOT carry the old htmx sse-extension
    attributes — in htmx 1.9.x those silently never fired, so live updates were
    dead and the page only changed on a manual reload."""
    await _seed_game(reset_db, game_id="G_live", state=GameState.ACTIVE)
    r = await client.get("/games/G_live")
    assert r.status_code == 200
    # The working wiring the EventSource reads off the live region.
    assert 'data-stream-url="/games/G_live/stream"' in r.text
    assert 'data-live-url="/games/G_live/live"' in r.text
    assert "turn_talked" in r.text
    # The dead htmx sse-extension wiring must be gone.
    assert 'hx-ext="sse"' not in r.text
    assert "sse-connect=" not in r.text
    assert 'hx-trigger="sse:' not in r.text


@pytest.mark.asyncio
async def test_finished_game_viewer_has_no_live_stream(client, reset_db):
    """A non-active game opens no stream: the live-update attributes are absent
    so the page never tries to connect to a stream that will deliver nothing."""
    await _seed_game(reset_db, game_id="G_done", state=GameState.COMPLETED)
    r = await client.get("/games/G_done")
    assert r.status_code == 200
    assert "data-stream-url=" not in r.text
    assert "data-live-url=" not in r.text


@pytest.mark.asyncio
async def test_repointed_lobby_links_resolve(client, reset_db):
    """Every internal "go to the lobby" link now targets /play/hoard-hurt-help;
    that target must resolve (no 404) so none of the repointed links break."""
    r = await client.get("/play/hoard-hurt-help")
    assert r.status_code == 200
