"""Lobby, agent management, and game-entry web tests."""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.engine.sim_presets import sim_presets
from app.engine.tokens import bot_key_lookup
from app.main import app
from app.models import Base, Agent, AgentKind, Connection, Match, GameState, Player, User
from app.models.match import MatchKind
from app.engine.sims import pack_profile_choices
from tests.factories import make_agent, make_connection, make_user, seat_player


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


def _signed_in_cookies(user_id: int) -> dict:
    """A Starlette session cookie marking this user as signed-in (prod secret)."""
    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _seed_user(reset_db: async_sessionmaker) -> User:
    async with reset_db() as db:
        u = await make_user(db)
        await db.commit()
        await db.refresh(u)
        return u


async def _seed_game(reset_db: async_sessionmaker, state=GameState.REGISTERING) -> Match:
    async with reset_db() as db:
        g = Match(
            id="G_001",
            name="Test Match",
            state=state,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add(g)
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
) -> tuple[Agent, str, int]:
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, k = await make_connection(db, u, key=key)
        agent, _ = await make_agent(db, u, connection=connection, name=name)
        connection.first_connected_at = datetime.now(timezone.utc)
        connection.last_seen_at = datetime.now(timezone.utc)
        await db.commit()
        return agent, k, connection.id


@pytest.mark.asyncio
async def test_lobby_renders_at_play_path(client, reset_db):
    # The HHH lobby moved off `/` (now the Agent Ludum marketing page) to
    # `/games/hoard-hurt-help`; the upcoming-games listing lives there now.
    await _seed_game(reset_db)
    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200
    assert "Test Match" in r.text


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


@pytest.mark.asyncio
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
    from app.routes.viewer_presentation import sample_replay_data

    data = json.loads(sample_replay_data())
    assert data["sample"] is True
    assert data["agents"]  # at least one agent
    assert data["turns"]  # at least one resolved turn
    assert "owners" in data  # rail byline map present (may be empty)


@pytest.mark.asyncio
async def test_homepage_falls_back_to_sample_replay(client, reset_db):
    # With no showcase game in the DB, the agent-ludum homepage still shows the
    # animated replay (seeded from the bundled sample) — not a dead placeholder.
    r = await client.get("/")
    assert r.status_code == 200
    assert 'id="rc-data"' in r.text  # the robot-circle data island is present
    assert '"sample": true' in r.text  # it's the bundled sample
    assert "al-rc-ph" not in r.text  # the static placeholder is NOT shown


@pytest.mark.asyncio
async def test_quiet_lobby_falls_back_to_sample_replay(client, reset_db):
    # No live and no finished showcase game: the quiet lobby plays the sample
    # replay instead of the "No game running" empty state.
    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200
    assert 'id="rc-data"' in r.text
    assert '"sample": true' in r.text
    assert "No game running right now" not in r.text


@pytest.mark.asyncio
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
                id=f"G_SIM_{i}",
                name=f"Sim Match {i}",
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
                    name=f"sim-{i}-{seat}",
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
    assert "Sim Match 5" not in r.text
    assert "Agent Match 4" in r.text
    assert "Sim Match 4" in r.text
    assert "2026-05-25T11:00:00Z" in r.text
    assert "2026-06-02T11:30:00Z" in r.text
    assert "See all" in r.text
    assert "Delete" not in r.text

    expanded = await client.get("/games/hoard-hurt-help?recent=all&sims=all&cancelled=all")
    assert expanded.status_code == 200
    assert "Agent Match 5" in expanded.text
    assert "Sim Match 5" in expanded.text
    assert "Show fewer" in expanded.text


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_lobby_polls_upcoming_every_minute(client, reset_db):
    # The lobby wires a 60s poller at the upcoming fragment endpoint so an open
    # page self-updates without a manual reload.
    await _seed_game(reset_db)
    r = await client.get("/games/hoard-hurt-help")
    assert r.status_code == 200
    assert 'hx-get="/games/hoard-hurt-help/upcoming"' in r.text
    assert "every 60s" in r.text


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_join_requires_sign_in(client, reset_db):
    await _seed_game(reset_db)
    r = await client.get("/games/hoard-hurt-help/matches/G_001/join", follow_redirects=False)
    assert r.status_code == 303
    assert "/auth/google/login" in r.headers["location"]


@pytest.mark.asyncio
async def test_create_agent_setup_shows_key_once(client, reset_db):
    user = await _seed_user(reset_db)
    r = await client.post(
        "/me/agents/new",
        cookies=_signed_in_cookies(user.id),
        follow_redirects=True,
        data={"provider": "claude", "nickname": "Atlas"},
    )
    assert r.status_code == 200
    assert "sk_conn_" in r.text
    assert "X-Connection-Key" in r.text
    assert "X-Agent-Key" not in r.text

    async with reset_db() as db:
        connections = (
            await db.execute(select(Connection).where(Connection.user_id == user.id))
        ).scalars().all()
        agents = (
            await db.execute(select(Agent).where(Agent.user_id == user.id))
        ).scalars().all()
    assert len(connections) == 1
    assert len(agents) == 0

    r2 = await client.get(
        f"/me/connections/{connections[0].id}", cookies=_signed_in_cookies(user.id)
    )
    assert r2.status_code == 200
    assert "sk_conn_" not in r2.text
    assert "Reissue" in r2.text


@pytest.mark.asyncio
async def test_preset_sims_auto_provision_and_show_separately(client, reset_db):
    user = await _seed_user(reset_db)
    cookies = _signed_in_cookies(user.id)
    presets = sim_presets()
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


@pytest.mark.asyncio
async def test_practice_arena_join_copy_mentions_agent_start(client, reset_db):
    user = await _seed_user(reset_db)
    cookies = _signed_in_cookies(user.id)
    await _seed_practice_arena(reset_db)

    r = await client.get("/games/hoard-hurt-help/matches/G_PA/join", cookies=cookies)
    assert r.status_code == 200
    assert "Starts when you add an agent" in r.text


@pytest.mark.asyncio
async def test_practice_arena_upcoming_copy_mentions_agent_start(client, reset_db):
    await _seed_practice_arena(reset_db)

    r = await client.get("/games/hoard-hurt-help/upcoming")
    assert r.status_code == 200
    assert "Starts when you add an agent" in r.text


@pytest.mark.asyncio
async def test_practice_arena_starts_when_player_joins(client, reset_db, monkeypatch):
    user = await _seed_user(reset_db)
    cookies = _signed_in_cookies(user.id)
    await _seed_practice_arena(reset_db)
    agent, _key, _connection_id = await _seed_agent(reset_db, user)
    monkeypatch.setattr("app.engine.scheduler.registry.start", lambda match_id: None)

    r = await client.post(
        "/games/hoard-hurt-help/matches/G_PA/join",
        data={"agent_id": agent.id, "display_name": "AI_joiner"},
        cookies=cookies,
        follow_redirects=False,
    )
    assert r.status_code == 303

    async with reset_db() as db:
        g = (await db.execute(select(Match).where(Match.id == "G_PA"))).scalar_one()
    assert g.state == GameState.ACTIVE
    assert g.started_at is not None


@pytest.mark.asyncio
async def test_create_sim_bot_shows_sim_profile(client, reset_db):
    user = await _seed_user(reset_db)
    choice = next(
        choice
        for choice in pack_profile_choices(include_hidden=False)
        if choice.pack_id == "mixed_20"
    )
    async with reset_db() as db:
        agent = Agent(
            user_id=user.id,
            kind=AgentKind.BOT,
            name="Sable",
            game="hoard-hurt-help",
            bot_profile_id=choice.id,
                bot_profile_name=choice.label,
            bot_strategy=choice.strategy,
            bot_truthfulness=choice.truthfulness,
            bot_trust_model=choice.trust_model,
            bot_seed=7,
            bot_version="v1",
        )
        db.add(agent)
        await db.flush()
        assert agent.kind == AgentKind.BOT
        assert agent.bot_strategy == choice.strategy
        assert agent.bot_truthfulness == choice.truthfulness
        assert agent.bot_trust_model == choice.trust_model
        assert agent.bot_seed == 7
        assert agent.bot_version == "v1"


@pytest.mark.asyncio
async def test_bot_detail_does_not_rotate_key(client, reset_db):
    """Regression: visiting the connection page must not change the key."""
    user = await _seed_user(reset_db)
    key = "sk_conn_" + "a" * 48
    agent, _returned_key, connection_id = await _seed_agent(reset_db, user, key=key)
    for _ in range(2):
        r = await client.get(
            f"/me/connections/{connection_id}", cookies=_signed_in_cookies(user.id)
        )
        assert r.status_code == 200
    async with reset_db() as db:
        connection = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
    assert connection.key_lookup == bot_key_lookup(key)
    assert agent.id is not None


@pytest.mark.asyncio
async def test_reissue_invalidates_old_key_anytime(client, reset_db):
    """Reissue is the deliberate path that changes the key — allowed any time."""
    user = await _seed_user(reset_db)
    game = await _seed_game(reset_db, state=GameState.ACTIVE)  # even mid-game
    key = "sk_conn_" + "b" * 48
    agent, _returned_key, connection_id = await _seed_agent(reset_db, user, key=key)
    # The agent is in the active game.
    async with reset_db() as db:
        db.add(
            Player(
                match_id=game.id,
                user_id=user.id,
                agent_id=agent.id,
                seat_name="AI_x",
            )
        )
        await db.commit()

    r = await client.post(
        f"/me/connections/{connection_id}/reissue",
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        connection = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
    assert connection.key_lookup != bot_key_lookup(key)  # old key no longer resolves


@pytest.mark.asyncio
async def test_enter_bot_into_game(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    agent, _returned_key, _connection_id = await _seed_agent(reset_db, user)
    r = await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"agent_id": agent.id, "display_name": "AI_qa"},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/games/hoard-hurt-help/matches/G_001"
    async with reset_db() as db:
        p = (
            await db.execute(select(Player).where(Player.match_id == "G_001"))
        ).scalar_one()
    assert p.agent_id == agent.id
    assert p.seat_name == f"{user.handle}/{agent.name}"


@pytest.mark.asyncio
async def test_join_ignores_bad_display_name(client, reset_db):
    """The posted display name no longer controls the public seat name."""
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    agent, _returned_key, _connection_id = await _seed_agent(reset_db, user)
    r = await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"agent_id": agent.id, "display_name": "fuckwit"},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        player = (
            await db.execute(select(Player).where(Player.match_id == "G_001"))
        ).scalar_one()
    assert player.seat_name == f"{user.handle}/{agent.name}"


@pytest.mark.asyncio
async def test_duplicate_bot_entry_blocked(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    agent, _returned_key, _connection_id = await _seed_agent(reset_db, user)
    cookies = _signed_in_cookies(user.id)
    await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"agent_id": agent.id, "display_name": "AI_a"},
        cookies=cookies,
        follow_redirects=False,
    )
    r = await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"agent_id": agent.id, "display_name": "AI_b"},
        cookies=cookies,
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert "already in this game" in r.text


@pytest.mark.asyncio
async def test_two_bots_one_game(client, reset_db):
    """A user fields multiple agents by running multiple connections."""
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    a1, _k1, _c1 = await _seed_agent(reset_db, user, name="One")
    a2, _k2, _c2 = await _seed_agent(reset_db, user, name="Two")
    cookies = _signed_in_cookies(user.id)
    for agent, name in [(a1, "AI_one"), (a2, "AI_two")]:
        r = await client.post(
            "/games/hoard-hurt-help/matches/G_001/join",
            data={"agent_id": agent.id, "display_name": name},
            cookies=cookies,
            follow_redirects=False,
        )
        assert r.status_code == 303
    async with reset_db() as db:
        players = (
            (await db.execute(select(Player).where(Player.match_id == "G_001")))
            .scalars()
            .all()
        )
    assert {p.seat_name for p in players} == {f"{user.handle}/One", f"{user.handle}/Two"}


@pytest.mark.asyncio
async def test_duplicate_display_name_does_not_block_join(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    a1, _k1, _c1 = await _seed_agent(reset_db, user, name="One")
    a2, _k2, _c2 = await _seed_agent(reset_db, user, name="Two")
    cookies = _signed_in_cookies(user.id)
    await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"agent_id": a1.id, "display_name": "Dup"},
        cookies=cookies,
        follow_redirects=False,
    )
    r = await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"agent_id": a2.id, "display_name": "Dup"},
        cookies=cookies,
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        players = (
            (await db.execute(select(Player).where(Player.match_id == "G_001")))
            .scalars()
            .all()
        )
    assert {p.seat_name for p in players} == {f"{user.handle}/One", f"{user.handle}/Two"}


@pytest.mark.asyncio
async def test_rename_bot(client, reset_db):
    user = await _seed_user(reset_db)
    agent, _returned_key, _connection_id = await _seed_agent(reset_db, user, name="OldName")
    r = await client.post(
        f"/me/agents/{agent.id}/rename",
        data={"name": "NewName"},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        agent_row = (await db.execute(select(Agent).where(Agent.id == agent.id))).scalar_one()
    assert agent_row.name == "NewName"


@pytest.mark.asyncio
async def test_rename_duplicate_blocked(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_agent(reset_db, user, name="Taken")
    agent, _returned_key, _connection_id = await _seed_agent(reset_db, user, name="Mine")
    r = await client.post(
        f"/me/agents/{agent.id}/rename",
        data={"name": "Taken"},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_my_games_lists_user_games(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    agent, _returned_key, _connection_id = await _seed_agent(reset_db, user)
    await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"agent_id": agent.id, "display_name": "AI_qa"},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    r = await client.get("/me/matches", cookies=_signed_in_cookies(user.id))
    assert r.status_code == 200
    assert "Test Match" in r.text
