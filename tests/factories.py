"""Shared test factories for the Connection/Agent model."""

from __future__ import annotations

from app.config import provider_for_model
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_connection_key
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.user import User


async def make_user(db, i: int = 0) -> User:
    # A normal user has a public handle (required to own an agent). Derived from
    # `i`, which already keys the unique google_sub/email, so handles stay unique
    # without new collisions. Tests that exercise the handle gate set/clear it
    # explicitly instead of relying on this default.
    user = User(
        google_sub=f"sub-{i}",
        email=f"u{i}@t.com",
        handle=f"agent{i}",
        handle_key=f"agent{i}",
    )
    db.add(user)
    await db.flush()
    return user


async def make_connection(
    db,
    user: User,
    *,
    provider: ConnectionProvider = ConnectionProvider.CLAUDE,
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    key: str | None = None,
    nickname: str | None = None,
    max_concurrent_games: int = 3,
    stall_threshold: int = 3,
) -> tuple[Connection, str]:
    plain_key = key or generate_connection_key()
    connection = Connection(
        user_id=user.id,
        provider=provider,
        nickname=nickname,
        key_lookup=bot_key_lookup(plain_key),
        key_hint=bot_key_hint(plain_key),
        status=status,
        max_concurrent_games=max_concurrent_games,
        stall_threshold=stall_threshold,
    )
    db.add(connection)
    await db.flush()
    # Mirror migration 0026: every connection gets one enabled provider row for
    # its legacy provider, so the new provider-coverage routing can serve it.
    db.add(
        ConnectionProviderRow(
            connection_id=connection.id,
            provider=provider,
            enabled=True,
            detected=False,
        )
    )
    await db.flush()
    return connection, plain_key


async def make_agent(
    db,
    user: User,
    *,
    connection: Connection | None = None,
    name: str | None = None,
    kind: AgentKind = AgentKind.AI,
    status: AgentStatus | None = None,
    model: str = "claude-haiku-4-5",
    strategy_text: str = "Play to win.",
    sim_profile_id: str | None = None,
    sim_profile_name: str | None = None,
    sim_strategy: str | None = None,
    sim_truthfulness: int | None = None,
    sim_trust_model: str | None = None,
    sim_seed: int | None = None,
    sim_version: str | None = None,
    sim_fixture_pack: str | None = None,
) -> tuple[Agent, AgentVersion | None]:
    # AI agents carry a stored provider (CHECK: non-archived AI ⇒ provider set);
    # bots never route by provider, so theirs stays None. Mirror prod: take the
    # connection's provider when attached, else derive from the model.
    agent_provider: ConnectionProvider | None = None
    if kind == AgentKind.AI:
        if connection is not None:
            agent_provider = connection.provider
        else:
            derived = provider_for_model(model)
            agent_provider = (
                ConnectionProvider(derived) if derived is not None else ConnectionProvider.CLAUDE
            )
    agent = Agent(
        user_id=user.id,
        provider=agent_provider,
        kind=kind,
        name=name or f"agent-{user.id}",
        game="hoard-hurt-help",
        status=status
        or (AgentStatus.ACTIVE if connection is not None else AgentStatus.PAUSED),
        bot_profile_id=sim_profile_id,
        bot_profile_name=sim_profile_name,
        bot_strategy=sim_strategy,
        bot_truthfulness=sim_truthfulness,
        bot_trust_model=sim_trust_model,
        bot_seed=sim_seed,
        bot_version=sim_version,
        bot_fixture_pack=sim_fixture_pack,
    )
    db.add(agent)
    await db.flush()

    version: AgentVersion | None = None
    if kind == AgentKind.AI:
        version = AgentVersion(
            agent_id=agent.id,
            version_no=1,
            model=model,
            strategy_text=strategy_text,
        )
        db.add(version)
        await db.flush()
        agent.current_version_id = version.id
        await db.flush()
    return agent, version


async def make_version(
    db,
    agent: Agent,
    *,
    version_no: int = 1,
    model: str = "claude-haiku-4-5",
    strategy_text: str = "Play to win.",
) -> AgentVersion:
    version = AgentVersion(
        agent_id=agent.id,
        version_no=version_no,
        model=model,
        strategy_text=strategy_text,
    )
    db.add(version)
    await db.flush()
    agent.current_version_id = version.id
    await db.flush()
    return version


async def make_match(
    db,
    match_id: str,
    *,
    state: GameState,
) -> Match:
    match = Match(
        id=match_id,
        name=f"Match {match_id}",
        game="hoard-hurt-help",
        state=state,
        per_turn_deadline_seconds=60,
    )
    db.add(match)
    await db.flush()
    return match


async def seat_player(
    db,
    match_id: str,
    seat_name: str,
    i: int = 0,
    user: User | None = None,
    key: str | None = None,
    *,
    connection: Connection | None = None,
    model: str = "claude-haiku-4-5",
) -> Player:
    """Create user + connection + agent + player for a game.

    The connection's plaintext key is stashed on `player._test_key`.
    """
    if user is None:
        user = await make_user(db, i)
    if connection is None:
        connection, key = await make_connection(db, user, key=key)
    agent, version = await make_agent(
        db,
        user,
        connection=connection,
        name=seat_name,
        model=model,
    )
    player = Player(
        match_id=match_id,
        user_id=user.id,
        agent_id=agent.id,
        agent_version_id=version.id if version is not None else None,
        seat_name=seat_name,
        model_self_report=version.model if version is not None else None,
    )
    db.add(player)
    await db.flush()
    setattr(player, "_test_key", key)
    setattr(player, "_test_connection", connection)
    return player


async def make_bot(
    db,
    user: User,
    name: str | None = None,
    key: str | None = None,
    *,
    kind: AgentKind = AgentKind.AI,
    sim_profile_id: str | None = None,
    sim_profile_name: str | None = None,
    sim_strategy: str | None = None,
    sim_truthfulness: int | None = None,
    sim_trust_model: str | None = None,
    sim_seed: int | None = None,
    sim_version: str | None = None,
    sim_fixture_pack: str | None = None,
) -> tuple[Agent, str]:
    """Back-compat wrapper used by older tests.

    It now returns an Agent, not the removed Bot model. Detached AI agents are
    created without a connection; scripted opponents use AgentKind.BOT.
    """
    agent, _ = await make_agent(
        db,
        user,
        name=name,
        kind=kind,
        connection=None,
        sim_profile_id=sim_profile_id,
        sim_profile_name=sim_profile_name,
        sim_strategy=sim_strategy,
        sim_truthfulness=sim_truthfulness,
        sim_trust_model=sim_trust_model,
        sim_seed=sim_seed,
        sim_version=sim_version,
        sim_fixture_pack=sim_fixture_pack,
    )
    return agent, (key or generate_connection_key())
