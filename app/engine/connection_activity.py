"""Bot onboarding signals: first-connection / first-move detection and the
status the bot detail page shows.

This powers the live connection handshake on the bot detail page (specs/005).
The platform records exactly one fact on the bot — ``first_connected_at`` — and
derives everything else (in a game? has it moved?) from existing player/turn
data, so there is no duplicated state to keep in sync.

Both signals are emitted on a per-bot pub/sub channel (``bot:{id}``) using the
same in-process broadcaster the spectator stream uses, so an open detail page
can update without a reload.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import broadcast
from app.aware_datetime import ensure_aware
from app.engine.connection_health import (
    LOOP_RUNNING_WINDOW_SECONDS,
    ConnectionHealth,
    ConnectionHealthStatus,
    _HEALTH_PRESENTATION,
    within_window,
    agent_is_defaulting,
    humanize_since,
)
from app.engine.onboarding_states import PREGAME_STATES, has_moved
from app.models.connection import Connection, ConnectionStatus
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.turn import TurnSubmission

# A "bot" here is the user's AI Connection (runner/MCP login). Aliased so the
# onboarding/health signatures read in bot terms while keeping the real type.
Bot = Connection
BotStatus = ConnectionStatus

# Don't rewrite last_seen_at on every fast poll — once per this interval is plenty.
_HEARTBEAT_THROTTLE_SECONDS = 10
# Same idea for the play-loop heartbeat (last_polled_at): get_next_turn can fire
# rapidly when idle (instant no_game reply), so throttle the write.
_POLL_THROTTLE_SECONDS = 10


def bot_channel(bot_id: int) -> str:
    """Pub/sub channel key for one bot's onboarding events."""
    return f"bot:{bot_id}"


class OnboardingState(str, enum.Enum):
    """Where a bot is on the connect -> playing path, for its detail page."""

    WAITING = "waiting"  # never connected, not in a game
    WAITING_IN_GAME = "waiting_in_game"  # entered in a game but not yet connected
    CONNECTED_NO_GAME = "connected_no_game"  # connected, idle — needs a game
    CONNECTED_PREGAME = "connected_pregame"  # connected, in a game that hasn't started
    IN_GAME_NO_MOVE = "in_game_no_move"  # connected, in an active game, no move yet
    PLAYING = "playing"  # has made at least one real move (established)


@dataclass
class OnboardingStatus:
    """Resolved onboarding state plus the game (if any) the panel should point at."""

    state: OnboardingState
    bot_name: str
    match_id: str | None = None
    game_name: str | None = None
    game_type: str | None = None


async def mark_seen(
    db: AsyncSession, bot: Bot, *, key_hash: str, now: datetime | None = None
) -> None:
    """Record an authenticated agent call: first-connect, key cutover, heartbeat.

    Called from the single auth choke point (``require_connection``), so it covers
    every connection method (runner, MCP, direct API) with one hook. Does four
    things in a single atomic UPDATE per call:

      * **Usage count** — bump ``api_call_count`` by one. Every authenticated call
        is one paid model inference in interactive (MCP) mode, so this is the raw
        cost signal the detail page shows. Counting it here, not on a throttle,
        keeps the count exact; folding it into the same UPDATE that already runs
        for the heartbeat avoids a second write per call (no write amplification).
      * **First connect** — on the ``NULL -> now`` transition of
        ``first_connected_at``, set it and announce ``connected`` (once; later
        calls are no-ops here).
      * **Key cutover** — if the call used the CURRENT key while a previous key is
        still live from a graceful rotation, clear ``prev_key_lookup`` so the old
        key stops working now that the new one is in use.
      * **Heartbeat** — refresh ``last_seen_at`` (throttled), the signal the
        health badge reads to tell "alive now" from "connected once".
    """
    now = now or datetime.now(timezone.utc)
    first = bot.first_connected_at is None
    cutover = key_hash == bot.key_lookup and bot.prev_key_lookup is not None
    heartbeat_due = (
        bot.last_seen_at is None
        or (now - ensure_aware(bot.last_seen_at)).total_seconds() >= _HEARTBEAT_THROTTLE_SECONDS
    )

    # One atomic UPDATE per call: always bump the call counter; conditionally set
    # the first-connect / cutover / heartbeat fields in the same statement. The
    # counter rides along with a write that (apart from the steady-state heartbeat
    # cadence) was happening anyway, so there is no extra round-trip per call.
    values: dict[str, object] = {
        "api_call_count": Connection.api_call_count + 1,
    }
    if first:
        values["first_connected_at"] = now
        if getattr(bot, "status", None) == ConnectionStatus.PENDING:
            values["status"] = ConnectionStatus.ACTIVE
    if cutover:
        values["prev_key_lookup"] = None
    if heartbeat_due:
        values["last_seen_at"] = now

    await db.execute(update(Connection).where(Connection.id == bot.id).values(**values))
    await db.commit()

    # Refresh the in-memory object from what the atomic UPDATE just wrote, so
    # callers that read these fields after auth see fresh values. We must NOT set
    # the attributes by hand here: ``api_call_count`` was bumped with a relative
    # ``col + 1`` expression the ORM can't track, so hand-assigning would mark the
    # object dirty and a later commit in the same request would write it a second
    # time (double-counting). Expiring forces a clean re-read instead.
    await db.refresh(bot)

    if first:
        await broadcast.publish(bot_channel(bot.id), "connected", {})


async def mark_polled(
    db: AsyncSession, connection: Connection, *, now: datetime | None = None
) -> None:
    """Record that the AI just polled get_next_turn — the play-loop heartbeat.

    Distinct from ``mark_seen``/``last_seen_at`` (which any authenticated call,
    even a sign-in handshake, bumps): ``last_polled_at`` only advances while an AI
    is actually running the play loop, so it's the honest "is this agent playing"
    signal used to gate seating. Throttled and absolute (no ``col + 1``), so a
    later commit in the same request can't double-write — one cheap UPDATE.
    """
    now = now or datetime.now(timezone.utc)
    last = connection.last_polled_at
    if (
        last is not None
        and (now - ensure_aware(last)).total_seconds() < _POLL_THROTTLE_SECONDS
    ):
        return
    await db.execute(
        update(Connection)
        .where(Connection.id == connection.id)
        .values(last_polled_at=now)
    )
    await db.commit()


async def increment_turns_played(db: AsyncSession, connection_id: int) -> None:
    """Bump a connection's lifetime ``turns_played`` by one.

    Called after a real (non-defaulted) move is committed, so the detail page can
    show how many turns this connection has actually played. One atomic UPDATE;
    the caller's own commit (or this one) persists it.
    """
    await db.execute(
        update(Connection)
        .where(Connection.id == connection_id)
        .values(turns_played=Connection.turns_played + 1)
    )
    await db.commit()


async def mark_first_move(db: AsyncSession, bot_id: int) -> None:
    """Announce the bot's first real move; no-op on every move after the first.

    Call this after the submission has been committed. "First" means exactly one
    non-defaulted submission now exists for the bot. Because the MCP
    ``submit_action`` tool proxies to the HTTP ``/submit`` endpoint, hooking the
    HTTP handler covers the MCP path too.
    """
    stmt = (
        select(TurnSubmission.id)
        .join(Player, Player.id == TurnSubmission.player_id)
        .where(Player.agent_id == bot_id, TurnSubmission.was_defaulted.is_(False))
        .limit(2)
    )
    real_submissions = (await db.execute(stmt)).all()
    if len(real_submissions) == 1:
        await broadcast.publish(bot_channel(bot_id), "moved", {})


async def _seated_matches(db: AsyncSession, bot_id: int) -> Sequence[Match]:
    """Every match this bot is currently seated in (not left).

    The one query behind both onboarding-state and health resolution, which each
    scan these to find the bot's active / pregame game.
    """
    return (
        (
            await db.execute(
                select(Match)
                .join(Player, Player.match_id == Match.id)
                .where(Player.agent_id == bot_id, Player.left_at.is_(None))
            )
        )
        .scalars()
        .all()
    )


async def compute_onboarding_status(db: AsyncSession, bot: Bot) -> OnboardingStatus:
    """Resolve the bot's onboarding state from its stored + derived facts.

    Precedence (top wins): has-moved -> in-active-game -> connected-in-pregame ->
    connected-no-game -> entered-but-waiting-to-connect -> waiting. Play history
    takes precedence so any established bot (including ones created before this
    feature, with a NULL ``first_connected_at``) resolves to "playing" — a state
    the detail page no longer renders as a persistent line (the health badge owns
    that), keeping it only as the one-time first-move flourish.
    """
    games = await _seated_matches(db, bot.id)
    active = next((g for g in games if g.state == GameState.ACTIVE), None)
    pregame = next((g for g in games if g.state in PREGAME_STATES), None)
    connected = bot.first_connected_at is not None
    # A Connection has no `name`; its display name is the user-set nickname (a
    # machine connection is named after the box) with a stable fallback.
    name = bot.nickname or "Machine connection"

    if await has_moved(db, bot.id):
        # Established bot. The detail page hides the onboarding panel entirely for
        # this state and lets the health badge be the single source of truth, so
        # this only surfaces as the one-time first-move "flourish". Point it only
        # at a genuinely live game — never a finished one, which would render a
        # dead "Watch live" link.
        return OnboardingStatus(
            OnboardingState.PLAYING,
            bot_name=name,
            match_id=active.id if active else None,
            game_name=active.name if active else None,
            game_type=active.game if active else None,
        )

    if connected:
        if active is not None:
            return OnboardingStatus(
                OnboardingState.IN_GAME_NO_MOVE,
                name,
                active.id,
                active.name,
                active.game,
            )
        if pregame is not None:
            return OnboardingStatus(
                OnboardingState.CONNECTED_PREGAME,
                name,
                pregame.id,
                pregame.name,
                pregame.game,
            )
        return OnboardingStatus(OnboardingState.CONNECTED_NO_GAME, name)

    waiting_game = active or pregame
    if waiting_game is not None:
        return OnboardingStatus(
            OnboardingState.WAITING_IN_GAME,
            name,
            waiting_game.id,
            waiting_game.name,
            waiting_game.game,
        )
    return OnboardingStatus(OnboardingState.WAITING, name)


async def compute_bot_health(
    db: AsyncSession, bot: Bot, *, now: datetime | None = None
) -> ConnectionHealthStatus:
    """Resolve a bot's operational health for the badge.

    Returns the shared ``ConnectionHealthStatus`` (with ``agent_count`` left at its
    default 0 — bot health does not surface a coverage count). It uses the same
    health enum and badge presentation as ``compute_connection_health`` but a
    different state machine and liveness signal, so the two compute functions stay
    distinct.

    Precedence (top wins): Paused (owner intent) -> Stalled (in a live game but
    the runner is cold or every recent move defaulted) -> Live (warm + in a game)
    -> Ready (warm, no game) -> Disconnected. "Warm" means the play-loop heartbeat
    (``last_polled_at``, bumped only by ``get_next_turn``) is within
    ``LOOP_RUNNING_WINDOW_SECONDS``. Using ``last_polled_at`` instead of
    ``last_seen_at`` prevents a one-off sign-in handshake from making a
    non-running connection appear "ready".
    """
    now = now or datetime.now(timezone.utc)
    warm = within_window(bot.last_polled_at, now, LOOP_RUNNING_WINDOW_SECONDS)
    last_connected = bot.last_seen_at or bot.first_connected_at
    never = last_connected is None
    last_connected_aware = ensure_aware(last_connected) if last_connected is not None else None
    human = None if last_connected is None else humanize_since(last_connected, now)

    def build(
        state: ConnectionHealth,
        *,
        game: Match | None = None,
        needs_reconnect: bool = False,
    ) -> ConnectionHealthStatus:
        label, css, pulse = _HEALTH_PRESENTATION[state]
        return ConnectionHealthStatus(
            state=state,
            label=label,
            badge_class=css,
            pulse=pulse,
            needs_reconnect=needs_reconnect,
            never_connected=never,
            last_connected_at=last_connected_aware,
            last_connected_human=human,
            match_id=game.id if game else None,
            game_name=game.name if game else None,
        )

    if bot.status == BotStatus.PAUSED:
        return build(ConnectionHealth.PAUSED)

    games = await _seated_matches(db, bot.id)
    active = next((g for g in games if g.state == GameState.ACTIVE), None)

    if active is not None:
        threshold = max(1, bot.stall_threshold)
        if not warm or await agent_is_defaulting(db, bot.id, active.id, threshold):
            return build(ConnectionHealth.STALLED, game=active, needs_reconnect=True)
        return build(ConnectionHealth.LIVE, game=active)

    if warm:
        return build(ConnectionHealth.READY)
    return build(ConnectionHealth.DISCONNECTED, needs_reconnect=True)
