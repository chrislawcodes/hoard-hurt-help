"""Routing tests for the Agent Ludum front page + the platform/game URL split.

`/` now serves the Agent Ludum marketing page; the Hoard·Hurt·Help lobby lives
at `/games/hoard-hurt-help`; the per-match viewer now uses
`/games/{game}/matches/{match_id}`.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import GameState, Match, Player
from app.models.agent import AgentKind
from tests.factories import make_agent, make_user


async def _seed_game(
    reset_db: async_sessionmaker,
    match_id: str = "G_001",
    name: str = "Test Match",
    state: GameState = GameState.REGISTERING,
) -> Match:
    async with reset_db() as db:
        g = Match(
            id=match_id,
            name=name,
            state=state,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add(g)
        await db.commit()
        await db.refresh(g)
        return g


async def _seed_leaderboard_match(
    reset_db: async_sessionmaker,
    *,
    match_id: str,
    name: str,
    scheduled_start: datetime,
    seat_specs: list[tuple[int, str, AgentKind, str | None, float, int]],
) -> None:
    async with reset_db() as db:
        match = Match(
            id=match_id,
            name=name,
            game="hoard-hurt-help",
            state=GameState.COMPLETED,
            scheduled_start=scheduled_start,
            completed_at=scheduled_start + timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add(match)
        await db.flush()

        winners: list[Player] = []
        for user_index, seat_name, kind, bot_profile_name, round_wins, total_score in seat_specs:
            user = await make_user(db, user_index)
            agent, _ = await make_agent(
                db,
                user,
                name=bot_profile_name or f"agent-{seat_name}",
                kind=kind,
                bot_profile_name=bot_profile_name if kind == AgentKind.BOT else None,
            )
            player = Player(match_id=match.id, user_id=user.id, agent_id=agent.id, seat_name=seat_name)
            db.add(player)
            await db.flush()
            player.total_round_wins = round_wins
            player.total_round_score = total_score
            player.current_round_score = total_score
            winners.append(player)

        match.winner_player_id = winners[0].id
        await db.commit()


async def _seed_leaderboard_data(reset_db: async_sessionmaker) -> None:
    await _seed_leaderboard_match(
        reset_db,
        match_id="G_new",
        name="June ranking",
        scheduled_start=datetime(2026, 6, 4, 12, tzinfo=timezone.utc),
        seat_specs=[
            (1, "Alpha", AgentKind.AI, None, 3.0, 120),
            (2, "Beta", AgentKind.AI, None, 2.0, 100),
            (3, "Gamma", AgentKind.BOT, "Random Bot", 1.0, 90),
        ],
    )
    await _seed_leaderboard_match(
        reset_db,
        match_id="G_old",
        name="Pre-cutoff ranking",
        scheduled_start=datetime(2026, 6, 2, 12, tzinfo=timezone.utc),
        seat_specs=[
            (10, "Old One", AgentKind.AI, None, 4.0, 200),
            (11, "Old Two", AgentKind.AI, None, 1.0, 10),
        ],
    )


@pytest.mark.asyncio
async def test_root_serves_agent_ludum_marketing(client, reset_db):
    """`/` is the Agent Ludum platform page with a CTA into the HHH lobby."""
    r = await client.get("/")
    assert r.status_code == 200
    assert "Agent" in r.text and "Ludum" in r.text
    # Stable brand descriptor — a durable marker of the marketing page that
    # doesn't couple the test to the churnable hero headline.
    assert "Benchmarks measure your agent" in r.text
    # The funnel: a primary CTA points into the play flow, not at `/`.
    assert 'href="/play"' in r.text
    assert 'href="/leaderboard"' in r.text
    assert 'al-nav-leaderboard' in r.text


@pytest.mark.asyncio
async def test_lobby_served_at_game_path(client, reset_db):
    """The HHH lobby (upcoming games etc.) now lives at /games/hoard-hurt-help."""
    await _seed_game(reset_db)
    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200
    assert "Test Match" in r.text  # the upcoming-games listing renders here
    assert 'href="/leaderboard"' in r.text
    assert 'al-nav-leaderboard' in r.text


@pytest.mark.asyncio
async def test_global_leaderboard_renders_rankings(client, reset_db):
    """The global leaderboard shows real rows and the top-level filters."""
    await _seed_leaderboard_data(reset_db)
    r = await client.get("/leaderboard")
    assert r.status_code == 200
    assert "Leaderboard" in r.text
    assert "Hoard · Hurt · Help" in r.text
    assert "Alpha" in r.text
    assert "Beta" in r.text
    assert "Gamma" not in r.text
    assert "Old One" not in r.text
    assert "Open lobby →" not in r.text
    assert "Scoped to this game." not in r.text
    assert "This section is where" not in r.text
    assert "First-place bonus" in r.text
    assert "Hide bot games" in r.text


@pytest.mark.asyncio
async def test_global_leaderboard_can_include_bots_and_hide_bot_games(client, reset_db):
    """The bot filter should show bots when enabled and hide bot sections when requested."""
    await _seed_leaderboard_data(reset_db)
    with_bots = await client.get("/leaderboard?included=all")
    assert with_bots.status_code == 200
    assert "Random Bot" in with_bots.text
    assert "lb-tag-sim" in with_bots.text

    hidden = await client.get("/leaderboard?included=all&hide_sim_games=1")
    assert hidden.status_code == 200
    assert "No ranked competitors yet for this filter." in hidden.text
    assert "Alpha" not in hidden.text
    assert "Random Bot" not in hidden.text


@pytest.mark.asyncio
async def test_games_catalog_omits_explanatory_box(client, reset_db):
    """The games catalog should stay focused on the lobby CTA, not a rationale box."""
    r = await client.get("/games")
    assert r.status_code == 200
    assert "Why this works" not in r.text
    assert "Game = the title. Match = one play of that title." not in r.text


@pytest.mark.asyncio
async def test_game_viewer_unchanged(client, reset_db):
    """The per-match viewer now uses /games/{game}/matches/{match_id}."""
    await _seed_game(reset_db, match_id="G_view", state=GameState.ACTIVE)
    r = await client.get("/games/hoard-hurt-help/matches/G_view")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_active_game_viewer_wires_live_sse(client, reset_db):
    """An active game exposes the SSE stream + live-fragment URLs the page's
    plain-JS EventSource needs, and must NOT carry the old htmx sse-extension
    attributes — in htmx 1.9.x those silently never fired, so live updates were
    dead and the page only changed on a manual reload."""
    await _seed_game(reset_db, match_id="G_live", state=GameState.ACTIVE)
    r = await client.get("/games/hoard-hurt-help/matches/G_live")
    assert r.status_code == 200
    # The working wiring the EventSource reads off the live region.
    assert 'data-stream-url="/games/hoard-hurt-help/matches/G_live/stream"' in r.text
    assert 'data-live-url="/games/hoard-hurt-help/matches/G_live/live"' in r.text
    assert "turn_talked" in r.text
    # The dead htmx sse-extension wiring must be gone.
    assert 'hx-ext="sse"' not in r.text
    assert "sse-connect=" not in r.text
    assert 'hx-trigger="sse:' not in r.text


@pytest.mark.asyncio
async def test_finished_game_viewer_has_no_live_stream(client, reset_db):
    """A non-active game opens no stream: the live-update attributes are absent
    so the page never tries to connect to a stream that will deliver nothing."""
    await _seed_game(reset_db, match_id="G_done", state=GameState.COMPLETED)
    r = await client.get("/games/hoard-hurt-help/matches/G_done")
    assert r.status_code == 200
    assert "data-stream-url=" not in r.text
    assert "data-live-url=" not in r.text


@pytest.mark.asyncio
async def test_repointed_lobby_links_resolve(client, reset_db):
    """Every internal "go to the lobby" link now targets /games/hoard-hurt-help;
    that target must resolve (no 404) so none of the repointed links break."""
    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200
