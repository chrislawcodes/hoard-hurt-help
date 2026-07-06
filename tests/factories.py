"""Shared test factories for the Connection/Agent model."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import provider_for_model
from app.engine.tokens import (
    bot_key_hint,
    bot_key_lookup,
    generate_connection_key,
    generate_turn_token,
)
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission
from app.models.user import User


async def make_user(db, i: int = 0, *, handle: str | None = None) -> User:
    # A normal user has a public handle (required to own an agent). Derived from
    # `i`, which already keys the unique google_sub/email, so handles stay unique
    # without new collisions. Tests that exercise the handle gate set/clear it
    # explicitly instead of relying on this default. `handle` overrides the
    # derived default for tests that assert on a specific handle (e.g. seat
    # names built from it).
    resolved_handle = handle or f"agent{i}"
    user = User(
        google_sub=f"sub-{i}",
        email=f"u{i}@t.com",
        handle=resolved_handle,
        handle_key=resolved_handle,
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
    last_seen_at: datetime | None = None,
    first_connected_at: datetime | None = None,
    mcp_connected_at: datetime | None = None,
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
        last_seen_at=last_seen_at,
        first_connected_at=first_connected_at,
        mcp_connected_at=mcp_connected_at,
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
    bot_profile_id: str | None = None,
    bot_profile_name: str | None = None,
    bot_strategy: str | None = None,
    bot_truthfulness: int | None = None,
    bot_trust_model: str | None = None,
    bot_seed: int | None = None,
    bot_version: str | None = None,
    bot_fixture_pack: str | None = None,
    create_version: bool = True,
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
        bot_profile_id=bot_profile_id,
        bot_profile_name=bot_profile_name,
        bot_strategy=bot_strategy,
        bot_truthfulness=bot_truthfulness,
        bot_trust_model=bot_trust_model,
        bot_seed=bot_seed,
        bot_version=bot_version,
        bot_fixture_pack=bot_fixture_pack,
    )
    db.add(agent)
    await db.flush()

    version: AgentVersion | None = None
    if kind == AgentKind.AI and create_version:
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
    name: str | None = None,
    scheduled_start: datetime | None = None,
    max_players: int | None = None,
    per_turn_deadline_seconds: int = 60,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    total_rounds: int | None = None,
    turns_per_round: int | None = None,
    current_round: int | None = None,
    current_turn: int | None = None,
    match_kind: str | None = None,
) -> Match:
    """Create + flush a Match row.

    `scheduled_start` defaults to one hour from now — a REGISTERING match that
    hasn't started, the shape most callers want. Pass an explicit value (e.g.
    `datetime.now(timezone.utc)`, or a time in the past) for an already-active
    or already-finished match. Every other optional field is only set on the
    row when given explicitly, so callers that don't care about it get the
    model's own default (e.g. `max_players=10`, `total_rounds=7`).
    """
    match = Match(
        id=match_id,
        name=name or f"Match {match_id}",
        game="hoard-hurt-help",
        state=state,
        scheduled_start=scheduled_start or (datetime.now(timezone.utc) + timedelta(hours=1)),
        per_turn_deadline_seconds=per_turn_deadline_seconds,
    )
    if max_players is not None:
        match.max_players = max_players
    if started_at is not None:
        match.started_at = started_at
    if completed_at is not None:
        match.completed_at = completed_at
    if total_rounds is not None:
        match.total_rounds = total_rounds
    if turns_per_round is not None:
        match.turns_per_round = turns_per_round
    if current_round is not None:
        match.current_round = current_round
    if current_turn is not None:
        match.current_turn = current_turn
    if match_kind is not None:
        match.match_kind = match_kind
    db.add(match)
    await db.flush()
    return match


async def make_turn(
    db,
    match_id: str,
    *,
    round: int = 1,
    turn: int = 1,
    phase: str = "act",
    resolved: bool = True,
    turn_token: str | None = None,
    opened_at: datetime | None = None,
    deadline_at: datetime | None = None,
    resolved_at: datetime | None = None,
    talk_resolved_at: datetime | None = None,
) -> Turn:
    """Create + flush a Turn row.

    Defaults to a resolved act turn (the shape read-model tests seed).
    `resolved=True` stamps `resolved_at` (at `opened_at` unless given
    explicitly); `resolved=False` leaves the turn open. `deadline_at` defaults
    to one minute after `opened_at`, which itself defaults to now.
    """
    opened = opened_at or datetime.now(timezone.utc)
    row = Turn(
        match_id=match_id,
        round=round,
        turn=turn,
        turn_token=turn_token or generate_turn_token(),
        opened_at=opened,
        deadline_at=deadline_at or (opened + timedelta(minutes=1)),
        phase=phase,
    )
    if resolved:
        row.resolved_at = resolved_at or opened
    elif resolved_at is not None:
        row.resolved_at = resolved_at
    if talk_resolved_at is not None:
        row.talk_resolved_at = talk_resolved_at
    db.add(row)
    await db.flush()
    return row


async def add_submission(
    db,
    turn: Turn,
    player: Player,
    *,
    action: str = "HOARD",
    target_player_id: int | None = None,
    message: str = "",
    thinking: str = "",
    points_delta: int | None = None,
    round_score_after: int | None = None,
    was_defaulted: bool = False,
    submitted_at: datetime | None = None,
) -> TurnSubmission:
    """Create + flush a TurnSubmission for `player` on `turn`.

    `points_delta` / `round_score_after` are set only when given, so callers
    that don't care get the model defaults (0) — same as a hand-built row.
    """
    sub = TurnSubmission(
        turn_id=turn.id,
        player_id=player.id,
        action=action,
        target_player_id=target_player_id,
        message=message,
        thinking=thinking,
        was_defaulted=was_defaulted,
        submitted_at=submitted_at,
    )
    if points_delta is not None:
        sub.points_delta = points_delta
    if round_score_after is not None:
        sub.round_score_after = round_score_after
    db.add(sub)
    await db.flush()
    return sub


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


async def seat_prebuilt_player(
    db,
    *,
    match: Match,
    user: User,
    agent: Agent,
    version: AgentVersion,
    seat_name: str,
    total_round_score: int = 0,
    current_round_score: int = 0,
) -> Player:
    """Seat an already-built user/agent/version as a Player in `match`.

    Unlike `seat_player` (which builds the user/connection/agent/version chain
    itself), this is for tests that already have all four objects on hand and
    just need the join row — the shape several files hand-rolled identically.
    """
    player = Player(
        match_id=match.id,
        user_id=user.id,
        agent_id=agent.id,
        agent_version_id=version.id,
        seat_name=seat_name,
        model_self_report=version.model,
        total_round_score=total_round_score,
        current_round_score=current_round_score,
    )
    db.add(player)
    await db.flush()
    return player


async def make_bot(
    db,
    user: User,
    name: str | None = None,
    key: str | None = None,
    *,
    kind: AgentKind = AgentKind.AI,
    bot_profile_id: str | None = None,
    bot_profile_name: str | None = None,
    bot_strategy: str | None = None,
    bot_truthfulness: int | None = None,
    bot_trust_model: str | None = None,
    bot_seed: int | None = None,
    bot_version: str | None = None,
    bot_fixture_pack: str | None = None,
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
        bot_profile_id=bot_profile_id,
        bot_profile_name=bot_profile_name,
        bot_strategy=bot_strategy,
        bot_truthfulness=bot_truthfulness,
        bot_trust_model=bot_trust_model,
        bot_seed=bot_seed,
        bot_version=bot_version,
        bot_fixture_pack=bot_fixture_pack,
    )
    return agent, (key or generate_connection_key())
