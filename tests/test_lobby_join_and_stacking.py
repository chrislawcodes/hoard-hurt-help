"""Join and seating semantics: entering agents into games, duplicate blocking,
admin/non-admin stacking, busy-AI rules, and bot rename.

Split out of test_lobby.py, which keeps the lobby-rendering tests. The
`reset_db` fixture and `_seed_*` helpers are duplicated from there (both
halves need them; conftest's `reset_db` is deliberately non-autouse).
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.engine.bots import pack_profile_choices
from app.engine.tokens import bot_key_lookup
from app.models import Base, Agent, AgentKind, Connection, Match, GameState, Player, User
from app.models.connection import ConnectionProvider
from app.models.user import UserRole
from tests.factories import make_agent, make_connection, make_match, make_user
from tests.conftest import signed_in_cookies as _signed_in_cookies


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


async def test_create_bot_shows_bot_profile(client, reset_db):
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


async def test_rotate_invalidates_old_key_anytime(client, reset_db):
    """Rotate is the deliberate path that changes the key — allowed any time."""
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
        f"/me/connections/{connection_id}/rotate",
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        connection = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
    assert connection.key_lookup != bot_key_lookup(key)  # old key no longer resolves


async def test_enter_bot_into_game(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    agent, _returned_key, _connection_id = await _seed_agent(reset_db, user)
    r = await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"chosen_provider": "claude", "agent_id": agent.id, "display_name": "AI_qa"},
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
    # Seat name is the agent name only — the owner's handle is never exposed.
    assert p.seat_name == agent.name
    assert user.handle not in p.seat_name


async def test_join_ignores_bad_display_name(client, reset_db):
    """The posted display name no longer controls the public seat name."""
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    agent, _returned_key, _connection_id = await _seed_agent(reset_db, user)
    r = await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"chosen_provider": "claude", "agent_id": agent.id, "display_name": "fuckwit"},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        player = (
            await db.execute(select(Player).where(Player.match_id == "G_001"))
        ).scalar_one()
    assert player.seat_name == agent.name


async def test_duplicate_bot_entry_blocked(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    agent, _returned_key, _connection_id = await _seed_agent(reset_db, user)
    cookies = _signed_in_cookies(user.id)
    await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"chosen_provider": "claude", "agent_id": agent.id, "display_name": "AI_a"},
        cookies=cookies,
        follow_redirects=False,
    )
    r = await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"chosen_provider": "claude", "agent_id": agent.id, "display_name": "AI_b"},
        cookies=cookies,
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert "already in this game" in r.text


async def test_two_bots_one_game(client, reset_db):
    """A user fields multiple agents in one game by giving each a different AI
    (one AI plays one seat at a time)."""
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    a1, _k1, _c1 = await _seed_agent(reset_db, user, name="One")
    a2, _k2, _c2 = await _seed_agent(
        reset_db, user, name="Two", provider=ConnectionProvider.GEMINI
    )
    cookies = _signed_in_cookies(user.id)
    for agent, name, ai in [(a1, "AI_one", "claude"), (a2, "AI_two", "gemini")]:
        r = await client.post(
            "/games/hoard-hurt-help/matches/G_001/join",
            data={"chosen_provider": ai, "agent_id": agent.id, "display_name": name},
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
    assert {p.seat_name for p in players} == {"One", "Two"}


async def test_admin_stacks_multiple_agents_in_one_submit(client, reset_db, monkeypatch):
    """An admin can tick several of their own agents and seat them in one POST."""
    monkeypatch.setattr(settings, "platform_admin_emails", "u0@t.com")
    user = await _seed_user(reset_db)  # make_user → u0@t.com
    await _seed_game(reset_db)
    a1, _k1, _c1 = await _seed_agent(reset_db, user, name="One")
    a2, _k2, _c2 = await _seed_agent(reset_db, user, name="Two")
    # The join form lists each agent with its own Join button (grouped by
    # provider). Admins can still stack several via repeated agent_id POSTs.
    form = await client.get(
        "/games/hoard-hurt-help/matches/G_001/join", cookies=_signed_in_cookies(user.id)
    )
    assert "One" in form.text and "Two" in form.text
    assert 'name="agent_id"' in form.text

    r = await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"chosen_provider": "claude", "agent_id": [a1.id, a2.id]},  # httpx repeats the field
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/games/hoard-hurt-help/matches/G_001"
    async with reset_db() as db:
        players = (
            (await db.execute(select(Player).where(Player.match_id == "G_001")))
            .scalars()
            .all()
        )
    # Pin the PROVIDER each seat got, not just the seat names. Asserting names
    # alone made this test pass in exactly the world spec risk R1 fears — every
    # agent silently seated on one AI. This body is the legacy "same AI for all"
    # admin shorthand; the lineup page can no longer produce it (it always posts
    # one provider per agent), so this pins the server contract only.
    assert {p.seat_name: p.chosen_provider for p in players} == {
        "One": "claude",
        "Two": "claude",
    }


async def test_join_form_shows_already_seated_agents(client, reset_db):
    """Re-entering the join form keeps every agent visible and marks seated ones."""
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    a1, _k1, _c1 = await _seed_agent(reset_db, user, name="One")
    a2, _k2, _c2 = await _seed_agent(reset_db, user, name="Two")
    cookies = _signed_in_cookies(user.id)
    await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"chosen_provider": "claude", "agent_id": a1.id},
        cookies=cookies,
        follow_redirects=False,
    )
    form = await client.get("/games/hoard-hurt-help/matches/G_001/join", cookies=cookies)
    assert "One" in form.text  # still visible
    assert "already in this game" in form.text  # seated agent is marked
    # Both agents are listed as lineup rows...
    assert f'data-agent-name="{a1.name}"' in form.text
    assert f'data-agent-id="{a2.id}"' in form.text
    # ...but the seated one renders NO checkbox and NO hidden mirrors, so it can
    # never contribute a stray agent_id to the posted lists (spec risk R3).
    seated = form.text[form.text.index(f'data-agent-name="{a1.name}"'):]
    seated = seated[:seated.index("</div>")]
    assert 'name="agent_id"' not in seated
    assert 'name="chosen_provider"' not in seated


async def test_match_page_shows_add_agent_affordance(client, reset_db):
    """The match page links back to the join form while registration is open."""
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    agent, _k, _c = await _seed_agent(reset_db, user, name="One")
    cookies = _signed_in_cookies(user.id)
    # Before joining: a signed-in viewer sees a "Join" call to action.
    page = await client.get("/games/hoard-hurt-help/matches/G_001", cookies=cookies)
    assert "Join" in page.text
    # After joining: the same page now offers to add another agent.
    await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"chosen_provider": "claude", "agent_id": agent.id},
        cookies=cookies,
        follow_redirects=False,
    )
    page2 = await client.get("/games/hoard-hurt-help/matches/G_001", cookies=cookies)
    assert "Add another agent" in page2.text
    assert "/games/hoard-hurt-help/matches/G_001/join" in page2.text


async def test_non_admin_stacks_agents_with_distinct_ais(client, reset_db):
    """A regular user may send several agents in one submit — each on a different AI."""
    user = await _seed_user(reset_db)  # not in the admin allowlist
    await _seed_game(reset_db)
    a1, _k1, _c1 = await _seed_agent(reset_db, user, name="One")  # Claude (live)
    a2, _k2, _c2 = await _seed_agent(
        reset_db, user, name="Two", provider=ConnectionProvider.GEMINI
    )  # Gemini (live)
    r = await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        # The screen posts one provider per agent, paired by position.
        data={"agent_id": [a1.id, a2.id], "chosen_provider": ["claude", "gemini"]},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/games/hoard-hurt-help/matches/G_001"
    async with reset_db() as db:
        seated = (
            (await db.execute(select(Player).where(Player.match_id == "G_001")))
            .scalars()
            .all()
        )
    assert {p.seat_name for p in seated} == {"One", "Two"}
    # Each seat kept the AI the user chose for it.
    assert {p.seat_name: p.chosen_provider for p in seated} == {
        "One": "claude",
        "Two": "gemini",
    }


async def test_non_admin_cannot_reuse_one_ai_across_agents(client, reset_db):
    """The same AI can't play two of a regular user's agents in one game — and nobody
    is seated when that's attempted, whether posted as a duplicate or as the legacy
    one-provider-for-all shorthand."""
    user = await _seed_user(reset_db)  # not in the admin allowlist
    await _seed_game(reset_db)
    a1, _k1, _c1 = await _seed_agent(reset_db, user, name="One")
    a2, _k2, _c2 = await _seed_agent(reset_db, user, name="Two")
    cookies = _signed_in_cookies(user.id)
    for data in (
        {"agent_id": [a1.id, a2.id], "chosen_provider": ["claude", "claude"]},
        {"agent_id": [a1.id, a2.id], "chosen_provider": "claude"},  # broadcast shorthand
    ):
        r = await client.post(
            "/games/hoard-hurt-help/matches/G_001/join",
            data=data,
            cookies=cookies,
            follow_redirects=False,
        )
        assert r.status_code == 409
        assert "different AI" in r.text
        async with reset_db() as db:
            count = len(
                (await db.execute(select(Player).where(Player.match_id == "G_001")))
                .scalars()
                .all()
            )
        assert count == 0
    # The picker is now per-agent AI chips (multi-select), not a single agent radio.
    form = await client.get(
        "/games/hoard-hurt-help/matches/G_001/join", cookies=cookies
    )
    assert 'name="agent_id"' in form.text  # the agent id still posts (hidden mirror)…
    assert 'class="agent-radio"' not in form.text  # …but the single-select radio is gone
    assert f'name="ai_for_{a1.id}"' in form.text  # each agent has its own AI picker
    assert f'name="ai_for_{a2.id}"' in form.text


async def _seed_agent_busy_in_active_match(reset_db, user) -> int:
    """One agent seated in an ACTIVE match, played by Claude.

    Claude is now the chosen AI of a not-finished game, so it reads as "busy" —
    "one AI = one game" blocks picking Claude again (admins may override). Seeds an
    open match G_B to join into. Returns the agent id.
    """
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        conn, _k = await make_connection(db, u)
        now = datetime.now(timezone.utc)
        conn.mcp_connected_at = now
        conn.first_connected_at = now
        conn.last_seen_at = now
        conn.last_polled_at = now  # AI is running the play loop → seats confirm
        agent, _v = await make_agent(db, u, connection=conn, name="Busy")
        active = Match(
            id="G_A", name="A", state=GameState.ACTIVE,
            scheduled_start=datetime.now(timezone.utc), per_turn_deadline_seconds=60,
        )
        open_match = Match(
            id="G_B", name="B", state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add_all([active, open_match])
        await db.flush()
        db.add(
            Player(
                match_id="G_A", user_id=u.id, agent_id=agent.id,
                seat_name=f"{u.handle}/Busy", chosen_provider="claude",
            )
        )
        await db.commit()
        return agent.id


async def test_admin_can_seat_agent_already_busy_at_capacity(client, reset_db, monkeypatch):
    """An admin can add an agent that is already in another match, past the cap."""
    monkeypatch.setattr(settings, "platform_admin_emails", "u0@t.com")
    user = await _seed_user(reset_db)  # u0@t.com
    agent_id = await _seed_agent_busy_in_active_match(reset_db, user)
    r = await client.post(
        "/games/hoard-hurt-help/matches/G_B/join",
        data={"chosen_provider": "claude", "agent_id": agent_id},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        seated = (
            await db.execute(select(Player).where(Player.match_id == "G_B"))
        ).scalars().all()
    assert [p.agent_id for p in seated] == [agent_id]


async def test_non_admin_still_blocked_by_busy_ai(client, reset_db):
    """A regular user can't reuse an AI already in a game — the bypass is admin-only."""
    user = await _seed_user(reset_db)  # not an admin
    agent_id = await _seed_agent_busy_in_active_match(reset_db, user)
    r = await client.post(
        "/games/hoard-hurt-help/matches/G_B/join",
        data={"chosen_provider": "claude", "agent_id": agent_id},
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert "already in a game" in r.text
    async with reset_db() as db:
        seated = (
            await db.execute(select(Player).where(Player.match_id == "G_B"))
        ).scalars().all()
    assert seated == []


async def test_duplicate_display_name_does_not_block_join(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_game(reset_db)
    a1, _k1, _c1 = await _seed_agent(reset_db, user, name="One")
    a2, _k2, _c2 = await _seed_agent(
        reset_db, user, name="Two", provider=ConnectionProvider.GEMINI
    )
    cookies = _signed_in_cookies(user.id)
    await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"chosen_provider": "claude", "agent_id": a1.id, "display_name": "Dup"},
        cookies=cookies,
        follow_redirects=False,
    )
    r = await client.post(
        "/games/hoard-hurt-help/matches/G_001/join",
        data={"chosen_provider": "gemini", "agent_id": a2.id, "display_name": "Dup"},
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
    assert {p.seat_name for p in players} == {"One", "Two"}


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


async def test_join_form_marks_busy_ai_in_another_game(client, reset_db):
    """An AI already in a different not-finished game shows as busy (and isn't
    pickable) on the join form for another game."""
    user = await _seed_user(reset_db)
    # Seeds Claude busy in the active match G_A, plus an open match G_B to join.
    await _seed_agent_busy_in_active_match(reset_db, user)
    r = await client.get(
        "/games/hoard-hurt-help/matches/G_B/join", cookies=_signed_in_cookies(user.id)
    )
    assert r.status_code == 200
    assert "▪ busy" in r.text  # the Claude row is greyed as busy
