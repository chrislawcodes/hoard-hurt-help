from __future__ import annotations

from datetime import datetime, timezone


from app.engine.bots import (
    BotContext,
    BotProfile,
    build_bot_profile,
    choose_bot_action_decision,
)
from app.models import (
    Agent,
    AgentKind,
    AgentVersion,
    Connection,
    ConnectionProvider,
    ConnectionStatus,
    GameState,
    Match,
    Player,
    User,
)
from app.read_models.leaderboard import load_leaderboard_sections
from app.schemas.agent import ScoreboardRow


async def _seed_user(db, index: int, handle: str | None = None) -> User:
    user = User(
        google_sub=f"sub-{index}",
        email=f"user{index}@example.com",
        handle=handle,
        handle_key=handle,
    )
    db.add(user)
    await db.flush()
    return user


async def _seed_connection(db, user: User) -> Connection:
    connection = Connection(
        user_id=user.id,
        provider=ConnectionProvider.CLAUDE,
        key_lookup=f"lookup-{user.id}",
        key_hint="abcd",
        status=ConnectionStatus.ACTIVE,
    )
    db.add(connection)
    await db.flush()
    return connection


async def _seed_bot_agent(db, user: User, *, name: str, seed: int) -> Agent:
    agent = Agent(
        user_id=user.id,
        kind=AgentKind.BOT,
        name=name,
        game="hoard-hurt-help",
        bot_profile_name=name,
        bot_strategy="leader_pressure",
        bot_truthfulness=80,
        bot_trust_model="even",
        bot_seed=seed,
        bot_version="v1",
    )
    db.add(agent)
    await db.flush()
    return agent


async def _seed_ai_agent(
    db,
    user: User,
    *,
    connection: Connection,
    name: str,
    model: str,
) -> tuple[Agent, AgentVersion]:
    agent = Agent(
        user_id=user.id,
        provider=connection.provider,
        kind=AgentKind.AI,
        name=name,
        game="hoard-hurt-help",
    )
    db.add(agent)
    await db.flush()
    version = AgentVersion(
        agent_id=agent.id,
        version_no=1,
        model=model,
        strategy_text="Play to win.",
    )
    db.add(version)
    await db.flush()
    agent.current_version_id = version.id
    await db.flush()
    return agent, version


async def _seed_match(db, *, name: str = "Ranked Match") -> Match:
    match = Match(
        id="M_bot_agents",
        name=name,
        state=GameState.COMPLETED,
        scheduled_start=datetime(2026, 6, 4, tzinfo=timezone.utc),
        started_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
        completed_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
        per_turn_deadline_seconds=60,
        game="hoard-hurt-help",
    )
    db.add(match)
    await db.flush()
    return match


async def test_bot_agent_and_ai_agent_kinds(reset_db) -> None:
    async with reset_db() as db:
        bot_owner = await _seed_user(db, 1, handle=None)
        ai_owner = await _seed_user(db, 2, handle="agent2")
        connection = await _seed_connection(db, ai_owner)
        bot = await _seed_bot_agent(db, bot_owner, name="Bot Alpha", seed=17)
        ai, _ = await _seed_ai_agent(db, ai_owner, connection=connection, name="Alpha", model="claude-sonnet")

        assert bot.kind == AgentKind.BOT
        assert ai.kind == AgentKind.AI
        # AI agent's provider is derived from the connection used to create it.
        assert ai.provider == connection.provider


async def test_bot_play_is_deterministic_without_connection_or_key(reset_db) -> None:
    async with reset_db() as db:
        owner = await _seed_user(db, 1, handle=None)
        bot = await _seed_bot_agent(db, owner, name="Bot Alpha", seed=77)
        profile = build_bot_profile(bot)
        context = BotContext(
            game_id="M_bot_agents",
            game_started_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
            round=1,
            turn=1,
            phase="act",
            your_agent_id="Bot Alpha",
            all_agent_ids=["Bot Alpha", "Alpha", "Beta"],
            history=[],
            scoreboard=[
                ScoreboardRow(agent_id="Bot Alpha", round_score=6, round_wins=0.0),
                ScoreboardRow(agent_id="Alpha", round_score=12, round_wins=0.0),
                ScoreboardRow(agent_id="Beta", round_score=4, round_wins=0.0),
            ],
            current_talk_messages=[],
        )

        first = choose_bot_action_decision(context, profile)
        second = choose_bot_action_decision(context, profile)

        assert isinstance(profile, BotProfile)
        assert first == second
        assert first.move == {"action": "HURT", "target_id": "Alpha"}


async def test_leaderboard_labels_ai_and_bot_rows_and_filters(reset_db) -> None:
    async with reset_db() as db:
        bot_owner = await _seed_user(db, 1, handle=None)
        ai_owner = await _seed_user(db, 2, handle="agent2")
        connection = await _seed_connection(db, ai_owner)
        bot_a = await _seed_bot_agent(db, bot_owner, name="Bot Alpha", seed=91)
        bot_b = await _seed_bot_agent(db, bot_owner, name="Bot Beta", seed=92)
        ai_a, version_a = await _seed_ai_agent(
            db,
            ai_owner,
            connection=connection,
            name="Alpha",
            model="claude-sonnet",
        )
        ai_b, version_b = await _seed_ai_agent(
            db,
            ai_owner,
            connection=connection,
            name="Beta",
            model="claude-haiku",
        )
        match = await _seed_match(db)
        db.add_all(
            [
                Player(
                    match_id=match.id,
                    user_id=bot_owner.id,
                    agent_id=bot_a.id,
                    seat_name="Bot Alpha",
                    total_round_wins=1.0,
                    total_round_score=18,
                ),
                Player(
                    match_id=match.id,
                    user_id=ai_owner.id,
                    agent_id=ai_a.id,
                    agent_version_id=version_a.id,
                    seat_name="Alpha",
                    total_round_wins=2.0,
                    total_round_score=24,
                    model_self_report="claude-sonnet",
                ),
                Player(
                    match_id=match.id,
                    user_id=bot_owner.id,
                    agent_id=bot_b.id,
                    seat_name="Bot Beta",
                    total_round_wins=0.0,
                    total_round_score=10,
                ),
                Player(
                    match_id=match.id,
                    user_id=ai_owner.id,
                    agent_id=ai_b.id,
                    agent_version_id=version_b.id,
                    seat_name="Beta",
                    total_round_wins=3.0,
                    total_round_score=30,
                    model_self_report="claude-haiku",
                ),
            ]
        )
        await db.commit()

    async with reset_db() as db:
        agents_sections = await load_leaderboard_sections(db, included="agents")
        bots_sections = await load_leaderboard_sections(db, included="bot")
        both_sections = await load_leaderboard_sections(db, included="all")

    def _rows(sections):
        return [row for section in sections for row in section.rows]

    agent_rows = _rows(agents_sections)
    bot_rows = _rows(bots_sections)
    both_rows = _rows(both_sections)

    # Display names no longer include the model — agents are name + strategy. The
    # provider that played is a separate badge (row.provider), tested elsewhere.
    assert [row.display_name for row in agent_rows] == ["Beta", "Alpha"]
    assert agent_rows[0].owner_handle == "agent2"
    assert agent_rows[0].is_bot is False

    assert [row.display_name for row in bot_rows] == ["Bot Alpha", "Bot Beta"]
    assert bot_rows[0].owner_handle is None
    assert bot_rows[0].is_bot is True

    labels = {row.display_name for row in both_rows}
    assert labels == {"Alpha", "Beta", "Bot Alpha", "Bot Beta"}
