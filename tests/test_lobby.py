"""Lobby, agent management, and game-entry web tests."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.db as app_db

from app.config import settings
from app.engine.bot_presets import bot_presets
from app.models import Agent, AgentKind, Connection, ConnectionSetup, Match, GameState, Player, User
from app.models.connection import ConnectionProvider
from app.models.match import MatchKind
from app.models.user import UserRole
from tests.factories import make_agent, make_connection, make_match, make_user, seat_player
from tests.conftest import signed_in_cookies as _signed_in_cookies


async def _seed_user(reset_db: async_sessionmaker) -> User:
    async with reset_db() as db:
        u = await make_user(db)
        u.role = (
            UserRole.ADMIN
            if u.email.lower() in settings.platform_admin_emails_set
            else UserRole.USER
        )
        await db.commit()
        await db.refresh(u)
        return u


async def _seed_game(reset_db: async_sessionmaker, state=GameState.REGISTERING) -> Match:
    async with reset_db() as db:
        g = await make_match(db, "G_001", state=state, name="Test Match")
        await db.commit()
        await db.refresh(g)
        return g


async def _seed_practice_arena(reset_db: async_sessionmaker) -> Match:
    async with reset_db() as db:
        g = Match(
            id="G_PA",
            name="Practice Arena",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) + timedelta(days=365),
            per_turn_deadline_seconds=60,
            min_players=1,
            max_players=10,
            match_kind=MatchKind.PRACTICE_ARENA.value,
        )
        db.add(g)
        await db.commit()
        await db.refresh(g)
        return g


async def _seed_agent(
    reset_db: async_sessionmaker,
    user: User,
    key: str | None = None,
    name: str = "Atlas",
    provider: ConnectionProvider = ConnectionProvider.CLAUDE,
) -> tuple[Agent, str, int]:
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, k = await make_connection(db, u, key=key, provider=provider)
        agent, _ = await make_agent(db, u, connection=connection, name=name)
        now = datetime.now(timezone.utc)
        existing_mcp = await db.scalar(
            select(Connection.id)
            .where(
                Connection.user_id == u.id,
                Connection.provider == connection.provider,
                Connection.mcp_connected_at.is_not(None),
                Connection.deleted_at.is_(None),
            )
            .limit(1)
        )
        if existing_mcp is None:
            connection.mcp_connected_at = now
        connection.first_connected_at = now
        connection.last_seen_at = now
        connection.last_polled_at = now  # AI is running the play loop → seats confirm
        await db.commit()
        return agent, k, connection.id


async def _seed_completed_showcase(reset_db: async_sessionmaker) -> None:
    """A finished 3-player game with one resolved turn — a watchable showcase."""
    from app.models import Turn, TurnSubmission

    async with reset_db() as db:
        g = Match(
            id="G_DONE",
            name="Finished Match",
            state=GameState.COMPLETED,
            scheduled_start=datetime.now(timezone.utc) - timedelta(hours=1),
            current_round=1,
            current_turn=1,
            per_turn_deadline_seconds=60,
        )
        db.add(g)
        await db.flush()
        players = [await seat_player(db, "G_DONE", f"AI_{i}", i=i) for i in range(3)]
        g.winner_player_id = players[0].id
        turn = Turn(
            match_id="G_DONE",
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
        for p in players:
            db.add(
                TurnSubmission(
                    turn_id=turn.id,
                    player_id=p.id,
                    action="HOARD",
                    message="banking a coin",
                    points_delta=2,
                    round_score_after=2,
                    was_defaulted=False,
                    submitted_at=datetime.now(timezone.utc),
                )
            )
        await db.commit()


async def test_lobby_shows_robot_replay_of_latest_game(client, reset_db):
    # With no live game, the lobby replays the latest finished showcase game
    # using the same robot-circle animation the front page uses.
    await _seed_completed_showcase(reset_db)
    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200
    assert 'id="rc-data"' in r.text  # the robot-circle data island
    assert "Animated Replay" in r.text
    assert "AI_0" in r.text  # agents from the finished game are in the replay data


def test_sample_replay_data_is_valid_rc_json() -> None:
    # The bundled fallback parses as rc_data the viewer can render: agents, turns,
    # and a sample flag so it can be told apart from a real game.
    from app.games.hoard_hurt_help.viewer import sample_replay_data

    data = json.loads(sample_replay_data())
    assert data["sample"] is True
    assert data["agents"]  # at least one agent
    assert data["turns"]  # at least one resolved turn
    assert "owners" in data  # rail byline map present (may be empty)


async def test_homepage_falls_back_to_sample_replay(client, reset_db):
    # With no showcase game in the DB, the agent-ludum homepage still shows the
    # animated replay (seeded from the bundled sample) — not a dead placeholder.
    r = await client.get("/")
    assert r.status_code == 200
    assert 'id="rc-data"' in r.text  # the robot-circle data island is present
    assert '"sample": true' in r.text  # it's the bundled sample
    assert "al-rc-ph" not in r.text  # the static placeholder is NOT shown


async def test_homepage_renders_with_live_and_finished_games(client, reset_db):
    # Regression guard for the homepage's per-game data. It now gathers player
    # counts, agent counts, and winners in three bulk queries instead of an N+1
    # loop; this confirms the live + finished-with-winner views still build.
    await _seed_completed_showcase(reset_db)  # G_DONE, COMPLETED, winner = AI_0
    await _seed_game(reset_db, state=GameState.ACTIVE)  # a live game in the mix
    r = await client.get("/")
    assert r.status_code == 200
    assert 'id="rc-data"' in r.text
    assert "AI_0" in r.text  # the finished showcase and its winner are rendered


@contextmanager
def _count_selects() -> Iterator[dict[str, int]]:
    """Count SELECTs the app issues against the patched test engine in-block."""
    counter = {"n": 0}

    def _on_exec(conn, cursor, statement, params, context, executemany) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    engine = app_db.engine
    event.listen(engine.sync_engine, "before_cursor_execute", _on_exec)
    try:
        yield counter
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _on_exec)


async def _seed_extra_completed(reset_db: async_sessionmaker, n: int) -> None:
    """A finished 3-agent game with a winner — adds to the lobby's recent list."""
    async with reset_db() as db:
        g = Match(
            id=f"G_EX{n}",
            name=f"Extra Match {n}",
            state=GameState.COMPLETED,
            scheduled_start=datetime.now(timezone.utc) - timedelta(hours=2 + n),
            per_turn_deadline_seconds=60,
        )
        db.add(g)
        await db.flush()
        base = 100 + n * 3
        players = [await seat_player(db, g.id, f"EX{n}_{j}", i=base + j) for j in range(3)]
        g.winner_player_id = players[0].id
        await db.commit()


async def test_lobby_query_count_flat_as_finished_games_grow(client, reset_db):
    # The lobby used to run a query per game for EVERY finished and cancelled
    # match. Now it reads them in one grouped query (via cache). Proof: the number
    # of DB SELECTs for the lobby must not grow as finished games pile up.
    await _seed_game(reset_db, state=GameState.ACTIVE)  # keeps showcase-replay off
    await _seed_extra_completed(reset_db, 0)
    with _count_selects() as first:
        r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200
    baseline = first["n"]

    for n in range(1, 7):  # six more finished games
        await _seed_extra_completed(reset_db, n)
    with _count_selects() as second:
        r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200

    # Query count doesn't grow with more finished games — the cache serves the
    # finished views (no per-game N+1). The second request may have fewer queries
    # if the cache is still warm.
    assert second["n"] <= baseline


async def test_quiet_lobby_falls_back_to_sample_replay(client, reset_db):
    # No live and no finished showcase game: the quiet lobby plays the sample
    # replay instead of the "No game running" empty state.
    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200
    assert 'id="rc-data"' in r.text
    assert '"sample": true' in r.text
    assert "No game running right now" not in r.text


async def test_lobby_recent_games_use_agent_names(client, reset_db):
    base = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    async with reset_db() as db:
        match = Match(
            id="G_RECENT",
            name="Recent Winner Match",
            state=GameState.COMPLETED,
            scheduled_start=base - timedelta(days=1),
            completed_at=base - timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add(match)
        await db.flush()
        bot_owner = await make_user(db, 501)
        agent, _ = await make_agent(
            db,
            bot_owner,
            name="Atlas",
            kind=AgentKind.BOT,
            bot_profile_name="Atlas",
            bot_strategy="coalition_seeker",
        )
        agent.name = f"{match.id}:Atlas"
        player = Player(
            match_id=match.id,
            user_id=bot_owner.id,
            agent_id=agent.id,
            seat_name="Atlas",
        )
        db.add(player)
        await db.flush()
        match.winner_player_id = player.id
        await db.commit()

    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200
    assert "Won by Atlas" in r.text
    assert f"{match.id}:Atlas" not in r.text


async def test_lobby_splits_recent_games_and_hides_delete(client, reset_db):
    base = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    async with reset_db() as db:
        for i in range(6):
            g = Match(
                id=f"G_AGENT_{i}",
                name=f"Agent Match {i}",
                state=GameState.COMPLETED,
                scheduled_start=base - timedelta(days=10 + i),
                per_turn_deadline_seconds=60,
            )
            db.add(g)
            await db.flush()
            players = []
            for seat in range(3):
                user = await make_user(db, 100 + (i * 10) + seat)
                kind = AgentKind.AI if seat < 2 else AgentKind.BOT
                agent, _ = await make_agent(
                    db,
                    user,
                    name=f"agent-{i}-{seat}",
                    kind=kind,
                )
                player = Player(
                    match_id=g.id,
                    user_id=user.id,
                    agent_id=agent.id,
                    seat_name=f"AI_{i}_{seat}",
                )
                db.add(player)
                await db.flush()
                players.append(player)
            g.winner_player_id = players[0].id
            g.completed_at = base - timedelta(days=10 + i, hours=1)

        for i in range(6):
            g = Match(
                id=f"G_BOT_{i}",
                name=f"Bot Match {i}",
                state=GameState.COMPLETED,
                scheduled_start=base - timedelta(days=20 + i),
                per_turn_deadline_seconds=60,
            )
            db.add(g)
            await db.flush()
            players = []
            for seat in range(2):
                user = await make_user(db, 300 + (i * 10) + seat)
                agent, _ = await make_agent(
                    db,
                    user,
                    name=f"bot-{i}-{seat}",
                    kind=AgentKind.BOT,
                )
                player = Player(
                    match_id=g.id,
                    user_id=user.id,
                    agent_id=agent.id,
                    seat_name=f"SIM_{i}_{seat}",
                )
                db.add(player)
                await db.flush()
                players.append(player)
            g.winner_player_id = players[0].id
            g.completed_at = base - timedelta(days=20 + i, hours=1)

        cancelled = Match(
            id="G_CANCELLED",
            name="Cancelled Match",
            state=GameState.CANCELLED,
            scheduled_start=base - timedelta(days=2),
            per_turn_deadline_seconds=60,
        )
        db.add(cancelled)
        await db.flush()
        cancelled.cancelled_at = base - timedelta(days=2, minutes=30)
        await db.commit()

    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200
    assert "Recent Games" in r.text
    assert "Recent Games with only Bots" in r.text
    assert "Cancelled Games" in r.text
    assert "Agent Match 5" not in r.text
    assert "Bot Match 5" not in r.text
    assert "Agent Match 4" in r.text
    assert "Bot Match 4" in r.text
    assert "2026-05-25T11:00:00Z" in r.text
    assert "2026-06-02T11:30:00Z" in r.text
    assert "See all" in r.text
    assert "Delete" not in r.text

    expanded = await client.get("/games/hoard-hurt-help?recent=all&sims=all&cancelled=all")
    assert expanded.status_code == 200
    assert "Agent Match 5" in expanded.text
    assert "Bot Match 5" in expanded.text
    assert "Show fewer" in expanded.text


async def test_lobby_cancels_overdue_unfilled_game(client, reset_db):
    # A game past its start time with too few players must not linger as
    # "Upcoming" with a live Join button. Viewing the lobby reconciles it to
    # CANCELLED, and it drops out of the upcoming list.
    async with reset_db() as db:
        db.add(
            Match(
                id="G_LATE",
                name="Wednesday Wild",
                state=GameState.REGISTERING,
                scheduled_start=datetime.now(timezone.utc) - timedelta(minutes=5),
                per_turn_deadline_seconds=60,
            )
        )
        await db.commit()

    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200
    assert "Cancelled Games" in r.text
    assert "Wednesday Wild" in r.text  # now shown in the cancelled section

    async with reset_db() as db:
        g = (await db.execute(select(Match).where(Match.id == "G_LATE"))).scalar_one()
    assert g.state == GameState.CANCELLED
    assert g.cancelled_at is not None


async def test_lobby_polls_upcoming_every_minute(client, reset_db):
    # The lobby wires a 60s poller at the upcoming fragment endpoint so an open
    # page self-updates without a manual reload.
    await _seed_game(reset_db)
    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200
    assert 'hx-get="/games/hoard-hurt-help/upcoming"' in r.text
    assert "every 60s" in r.text


async def test_upcoming_fragment_reconciles_and_lists(client, reset_db):
    # The polled fragment lists upcoming games and, on each fetch, cancels a game
    # that is past its start time with too few players.
    async with reset_db() as db:
        db.add(
            Match(
                id="G_SOON",
                name="Future Match",
                state=GameState.REGISTERING,
                scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
                per_turn_deadline_seconds=60,
            )
        )
        db.add(
            Match(
                id="G_LATE",
                name="Wednesday Wild",
                state=GameState.REGISTERING,
                scheduled_start=datetime.now(timezone.utc) - timedelta(minutes=5),
                per_turn_deadline_seconds=60,
            )
        )
        await db.commit()

    r = await client.get("/games/hoard-hurt-help/upcoming")
    assert r.status_code == 200
    assert "Future Match" in r.text  # still upcoming → listed
    assert "Wednesday Wild" not in r.text  # overdue + under-filled → cancelled

    async with reset_db() as db:
        late = (await db.execute(select(Match).where(Match.id == "G_LATE"))).scalar_one()
        soon = (await db.execute(select(Match).where(Match.id == "G_SOON"))).scalar_one()
    assert late.state == GameState.CANCELLED
    assert soon.state == GameState.REGISTERING


async def test_join_requires_sign_in(client, reset_db):
    await _seed_game(reset_db)
    r = await client.get("/games/hoard-hurt-help/matches/G_001/join", follow_redirects=False)
    assert r.status_code == 303
    assert "/auth/google/login" in r.headers["location"]


async def test_create_agent_setup_shows_key_once(client, reset_db):
    user = await _seed_user(reset_db)
    # The connections page mints one pending machine setup and shows its key inline.
    r = await client.get(
        "/me/connections",
        cookies=_signed_in_cookies(user.id),
    )
    assert r.status_code == 200
    assert "sk_conn_" in r.text
    assert "--install" in r.text
    assert "X-Agent-Key" not in r.text

    async with reset_db() as db:
        setups = (
            await db.execute(
                select(ConnectionSetup).where(ConnectionSetup.user_id == user.id)
            )
        ).scalars().all()
        agents = (
            await db.execute(select(Agent).where(Agent.user_id == user.id))
        ).scalars().all()
    assert len(setups) == 1
    assert len(agents) == 0


async def test_preset_bots_auto_provision_and_show_separately(client, reset_db):
    user = await _seed_user(reset_db)
    cookies = _signed_in_cookies(user.id)
    presets = bot_presets()
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        for idx, preset in enumerate(presets, start=1):
            agent = Agent(
                user_id=u.id,
                kind=AgentKind.BOT,
                name=preset.name,
                game="hoard-hurt-help",
                bot_profile_id=preset.id,
                bot_profile_name=preset.name,
                bot_strategy=preset.strategy,
                bot_truthfulness=preset.truthfulness,
                bot_trust_model=preset.trust_model,
                bot_seed=idx,
                bot_version="v1",
            )
            db.add(agent)
            await db.flush()
        await db.commit()

    r = await client.get("/me/agents", cookies=cookies)
    assert r.status_code == 200
    assert "No agents yet" in r.text

    async with reset_db() as db:
        bots = (
            await db.execute(
                select(Agent).where(
                    Agent.user_id == user.id,
                    Agent.kind == AgentKind.BOT,
                    Agent.archived_at.is_(None),
                )
            )
        ).scalars().all()
    assert len(bots) == len(presets)
    assert {bot.bot_profile_id for bot in bots} == {preset.id for preset in presets}
    assert {bot.bot_profile_name for bot in bots} == {preset.name for preset in presets}
    expected_names = {preset.name for preset in presets}
    assert {bot.name for bot in bots} == expected_names
    assert all(bot.name not in r.text for bot in bots)


async def test_practice_arena_join_copy_mentions_join_start(client, reset_db):
    user = await _seed_user(reset_db)
    cookies = _signed_in_cookies(user.id)
    await _seed_practice_arena(reset_db)
    # The join page is now a smart hub: it only renders the join form when the
    # user has a live, seatable agent. Give them one so we reach the copy below.
    await _seed_agent(reset_db, user)

    r = await client.get("/games/hoard-hurt-help/matches/G_PA/join", cookies=cookies)
    assert r.status_code == 200
    assert "Game starts when you join" in r.text
    assert "registered" in r.text


async def test_practice_arena_upcoming_copy_mentions_join_start(client, reset_db):
    await _seed_practice_arena(reset_db)

    r = await client.get("/games/hoard-hurt-help/upcoming")
    assert r.status_code == 200
    assert "Game starts when you join" in r.text
    assert "registered" in r.text


async def test_practice_arena_match_page_copy_mentions_join_start(client, reset_db):
    await _seed_practice_arena(reset_db)

    r = await client.get("/games/hoard-hurt-help/matches/G_PA")
    assert r.status_code == 200
    assert "Game starts when you join" in r.text
    assert "Starts <time" not in r.text


async def test_practice_arena_starts_when_player_joins(client, reset_db, monkeypatch):
    user = await _seed_user(reset_db)
    cookies = _signed_in_cookies(user.id)
    await _seed_practice_arena(reset_db)
    agent, _key, _connection_id = await _seed_agent(reset_db, user)
    monkeypatch.setattr("app.engine.scheduler.registry.start", lambda match_id: None)

    r = await client.post(
        "/games/hoard-hurt-help/matches/G_PA/join",
        data={"chosen_provider": "claude", "agent_id": agent.id, "display_name": "AI_joiner"},
        cookies=cookies,
        follow_redirects=False,
    )
    assert r.status_code == 303

    async with reset_db() as db:
        g = (await db.execute(select(Match).where(Match.id == "G_PA"))).scalar_one()
    assert g.state == GameState.ACTIVE
    assert g.started_at is not None


async def test_my_games_lists_user_games(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    agent, _returned_key, _connection_id = await _seed_agent(reset_db, user)
    await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"chosen_provider": "claude", "agent_id": agent.id, "display_name": "AI_qa"},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    r = await client.get("/me/matches", cookies=_signed_in_cookies(user.id))
    assert r.status_code == 200
    assert "Test Match" in r.text


# ---------------------------------------------------------------------------
# Exception-narrowing tests (fail-loudly cleanup)
# ---------------------------------------------------------------------------


async def test_db_error_building_replay_falls_back_to_sample(
    client: AsyncClient, reset_db: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SQLAlchemyError while building the showcase replay is caught, logged,
    and the page still renders with the sample fallback — not a 500."""
    await _seed_completed_showcase(reset_db)

    # Monkeypatch _game_view_context (called inside _build_showcase_replay) to
    # simulate a transient DB failure after the match has already been found.
    async def _raise_db_error(*args: object, **kwargs: object) -> dict:
        raise SQLAlchemyError("simulated DB connection error")

    monkeypatch.setattr(
        "app.routes.showcase_replay._game_view_context",
        _raise_db_error,
    )

    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200
    # The page renders, but falls back to the bundled sample replay.
    assert 'id="rc-data"' in r.text
    assert '"sample": true' in r.text


async def test_programming_error_building_replay_propagates(
    client: AsyncClient, reset_db: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A programming bug (TypeError, AttributeError, etc.) inside the replay
    builder must NOT be silently swallowed — it should propagate so it gets
    noticed, not hidden behind the sample fallback forever."""
    await _seed_completed_showcase(reset_db)

    async def _raise_type_error(*args: object, **kwargs: object) -> dict:
        raise TypeError("simulated programming bug: wrong argument type")

    monkeypatch.setattr(
        "app.routes.showcase_replay._game_view_context",
        _raise_type_error,
    )

    # The narrowed except clause only catches SQLAlchemyError, so TypeError
    # propagates out of the route and FastAPI returns a 500.
    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 500


async def test_db_error_during_reconciliation_still_renders_lobby(
    client: AsyncClient, reset_db: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SQLAlchemyError during cancel_overdue_unfilled_games is caught, logged,
    and the lobby still renders with whatever state the DB already holds."""
    await _seed_game(reset_db)

    async def _raise_db_error(db: object) -> int:
        raise SQLAlchemyError("simulated DB error in reconciliation")

    monkeypatch.setattr(
        "app.routes.web_lobby.cancel_overdue_unfilled_games",
        _raise_db_error,
    )

    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200
    assert "Test Match" in r.text  # DB state still rendered despite reconcile failure


async def test_programming_error_during_reconciliation_propagates(
    client: AsyncClient, reset_db: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A programming bug inside cancel_overdue_unfilled_games must propagate,
    not be silently ignored. The narrowed except only covers SQLAlchemyError."""
    await _seed_game(reset_db)

    async def _raise_attr_error(db: object) -> int:
        raise AttributeError("simulated programming bug: bad attribute access")

    monkeypatch.setattr(
        "app.routes.web_lobby.cancel_overdue_unfilled_games",
        _raise_attr_error,
    )

    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 500


async def test_db_error_during_upcoming_reconciliation_still_renders(
    client: AsyncClient, reset_db: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The polled /upcoming fragment catches SQLAlchemyError from reconciliation
    and renders current state rather than returning a 500."""
    await _seed_game(reset_db)

    async def _raise_db_error(db: object) -> int:
        raise SQLAlchemyError("simulated DB error in upcoming reconciliation")

    monkeypatch.setattr(
        "app.routes.web_lobby.cancel_overdue_unfilled_games",
        _raise_db_error,
    )

    r = await client.get("/games/hoard-hurt-help/upcoming")
    assert r.status_code == 200
    assert "Test Match" in r.text


async def test_programming_error_during_upcoming_reconciliation_propagates(
    client: AsyncClient, reset_db: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A programming bug in reconciliation propagates from the /upcoming fragment
    just as it does from the full lobby page."""
    await _seed_game(reset_db)

    async def _raise_key_error(db: object) -> int:
        raise KeyError("simulated programming bug: missing key")

    monkeypatch.setattr(
        "app.routes.web_lobby.cancel_overdue_unfilled_games",
        _raise_key_error,
    )

    r = await client.get("/games/hoard-hurt-help/upcoming")
    assert r.status_code == 500


async def test_upcoming_fragment_survives_null_scheduled_start(client, reset_db, monkeypatch):
    """A legacy row with scheduled_start=NULL must not 500 the upcoming fragment.

    The DB schema enforces NOT NULL today, but defensive rendering ensures a stale
    row in a live database (or a future schema change) can't take the page down.
    """
    from app.routes import web_lobby

    async def _fake_upcoming(_db):
        return [
            {
                "id": "G_NULL",
                "game_type": "hoard-hurt-help",
                "name": "Legacy Broken",
                "match_kind": "manual",
                "scheduled_start": None,
                "max_players": 10,
                "player_count": 0,
            }
        ]

    monkeypatch.setattr(web_lobby, "_upcoming_views", _fake_upcoming)

    r = await client.get("/games/hoard-hurt-help/upcoming")
    assert r.status_code == 200
    assert "Legacy Broken" in r.text


async def test_upcoming_fragment_renders_raw_datetime(client, reset_db):
    """The upcoming fragment must render a raw datetime object via the localdt filter.

    Before this fix, _upcoming_views passed pre-formatted ISO strings to the template.
    Now it passes raw datetime objects; this test confirms the Jinja filter handles them.
    """
    await _seed_game(reset_db)
    r = await client.get("/games/hoard-hurt-help/upcoming")
    assert r.status_code == 200
    assert "Test Match" in r.text
    assert 'class="localtime"' in r.text


