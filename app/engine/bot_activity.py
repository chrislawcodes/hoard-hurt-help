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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import broadcast
from app.models.bot import Bot, BotStatus
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission

_PREGAME_STATES = (GameState.SCHEDULED, GameState.REGISTERING)

# How long after the last authenticated call a bot still counts as "live". The
# runner polls every few seconds (idle) up to a ~40s long-poll, so this tolerates
# a couple of missed pings before the badge flips to disconnected.
_LIVE_WINDOW_SECONDS = 90
# Don't rewrite last_seen_at on every fast poll — once per this interval is plenty.
_HEARTBEAT_THROTTLE_SECONDS = 10


def _as_aware(dt: datetime) -> datetime:
    """SQLite drops tz info on read; treat a naive value as UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


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


async def _has_moved(db: AsyncSession, bot_id: int) -> bool:
    """True if any of the bot's players has a real (non-defaulted) submission."""
    stmt = (
        select(TurnSubmission.id)
        .join(Player, Player.id == TurnSubmission.player_id)
        .where(Player.bot_id == bot_id, TurnSubmission.was_defaulted.is_(False))
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None


async def mark_seen(
    db: AsyncSession, bot: Bot, *, key_hash: str, now: datetime | None = None
) -> None:
    """Record an authenticated agent call: first-connect, key cutover, heartbeat.

    Called from the single auth choke point (``require_bot``), so it covers every
    connection method (runner, MCP, direct API) with one hook. Commits once if
    anything changed, and does three things:

      * **First connect** — on the ``NULL -> now`` transition of
        ``first_connected_at``, set it and announce ``connected`` (once; later
        calls are no-ops here).
      * **Key cutover** — if the call used the CURRENT key while a previous key is
        still live from a graceful reissue, clear ``prev_key_lookup`` so the old
        key stops working now that the new one is in use.
      * **Heartbeat** — refresh ``last_seen_at`` (throttled), the signal the
        health badge reads to tell "alive now" from "connected once".
    """
    now = now or datetime.now(timezone.utc)
    first = bot.first_connected_at is None
    changed = False
    if first:
        bot.first_connected_at = now
        changed = True
    if key_hash == bot.key_lookup and bot.prev_key_lookup is not None:
        bot.prev_key_lookup = None
        changed = True
    if (
        bot.last_seen_at is None
        or (now - _as_aware(bot.last_seen_at)).total_seconds() >= _HEARTBEAT_THROTTLE_SECONDS
    ):
        bot.last_seen_at = now
        changed = True
    if changed:
        await db.commit()
    if first:
        await broadcast.publish(bot_channel(bot.id), "connected", {})


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
        .where(Player.bot_id == bot_id, TurnSubmission.was_defaulted.is_(False))
        .limit(2)
    )
    real_submissions = (await db.execute(stmt)).all()
    if len(real_submissions) == 1:
        await broadcast.publish(bot_channel(bot_id), "moved", {})


async def compute_onboarding_status(db: AsyncSession, bot: Bot) -> OnboardingStatus:
    """Resolve the bot's onboarding state from its stored + derived facts.

    Precedence (top wins): has-moved -> in-active-game -> connected-in-pregame ->
    connected-no-game -> entered-but-waiting-to-connect -> waiting. Play history
    takes precedence so any established bot (including ones created before this
    feature, with a NULL ``first_connected_at``) resolves to "playing" — a state
    the detail page no longer renders as a persistent line (the health badge owns
    that), keeping it only as the one-time first-move flourish.
    """
    games = (
        (
            await db.execute(
                select(Match)
                .join(Player, Player.match_id == Match.id)
                .where(Player.bot_id == bot.id, Player.left_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    active = next((g for g in games if g.state == GameState.ACTIVE), None)
    pregame = next((g for g in games if g.state in _PREGAME_STATES), None)
    connected = bot.first_connected_at is not None

    if await _has_moved(db, bot.id):
        # Established bot. The detail page hides the onboarding panel entirely for
        # this state and lets the health badge be the single source of truth, so
        # this only surfaces as the one-time first-move "flourish". Point it only
        # at a genuinely live game — never a finished one, which would render a
        # dead "Watch live" link.
        return OnboardingStatus(
            OnboardingState.PLAYING,
            bot_name=bot.name,
            match_id=active.id if active else None,
            game_name=active.name if active else None,
        )

    if connected:
        if active is not None:
            return OnboardingStatus(
                OnboardingState.IN_GAME_NO_MOVE, bot.name, active.id, active.name
            )
        if pregame is not None:
            return OnboardingStatus(
                OnboardingState.CONNECTED_PREGAME, bot.name, pregame.id, pregame.name
            )
        return OnboardingStatus(OnboardingState.CONNECTED_NO_GAME, bot.name)

    waiting_game = active or pregame
    if waiting_game is not None:
        return OnboardingStatus(
            OnboardingState.WAITING_IN_GAME, bot.name, waiting_game.id, waiting_game.name
        )
    return OnboardingStatus(OnboardingState.WAITING, bot.name)


class BotHealth(str, enum.Enum):
    """Operational health for the My Bots badge — 'is it working right now?'.

    Unlike ``BotStatus`` (the owner's on/off intent) this is derived from real
    activity: a bot reads green only when its runner is actually alive.
    """

    PAUSED = "paused"  # owner switched it off
    STALLED = "stalled"  # in a live game but not playing — runner down or failing
    LIVE = "live"  # connected and playing
    READY = "ready"  # connected, nothing to play right now
    DISCONNECTED = "disconnected"  # runner isn't running


# state -> (label, badge css class, pulsing dot). Green = alive, red = down,
# grey = off. The badge template is kept dumb: it just renders these.
_HEALTH_PRESENTATION: dict[BotHealth, tuple[str, str, bool]] = {
    BotHealth.PAUSED: ("Paused", "badge-done", False),
    BotHealth.STALLED: ("Stalled", "badge-alert", True),
    BotHealth.LIVE: ("Live", "badge-ok", True),
    BotHealth.READY: ("Ready", "badge-ok", False),
    BotHealth.DISCONNECTED: ("Disconnected", "badge-alert", False),
}


@dataclass
class BotHealthStatus:
    """Resolved health state plus everything the badge + reconnect block render."""

    state: BotHealth
    label: str
    badge_class: str
    pulse: bool
    needs_reconnect: bool  # Stalled/Disconnected → surface the reconnect prompt
    never_connected: bool
    last_connected_at: datetime | None
    last_connected_human: str | None  # "4m ago" / "2h ago", or None if never
    match_id: str | None = None
    game_name: str | None = None


def _humanize_since(dt: datetime, now: datetime) -> str:
    """Plain 'time ago' for the badge, e.g. 'just now', '4m ago', '2h ago'."""
    secs = int((now - _as_aware(dt)).total_seconds())
    if secs < 10:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


async def _is_defaulting(
    db: AsyncSession, bot_id: int, match_id: str, threshold: int
) -> bool:
    """True if the bot's last ``threshold`` submissions in this game all defaulted.

    Catches the runner-alive-but-failing case: it keeps polling (so the heartbeat
    stays warm) yet every move lands as a default. Bounded to ``threshold`` rows.
    """
    flags = (
        (
            await db.execute(
                select(TurnSubmission.was_defaulted)
                .join(Turn, Turn.id == TurnSubmission.turn_id)
                .join(Player, Player.id == TurnSubmission.player_id)
                .where(Player.bot_id == bot_id, Player.match_id == match_id)
                .order_by(Turn.round.desc(), Turn.turn.desc())
                .limit(threshold)
            )
        )
        .scalars()
        .all()
    )
    return len(flags) >= threshold and all(flags)


async def compute_bot_health(
    db: AsyncSession, bot: Bot, *, now: datetime | None = None
) -> BotHealthStatus:
    """Resolve a bot's operational health for the badge.

    Precedence (top wins): Paused (owner intent) -> Stalled (in a live game but
    the runner is cold or every recent move defaulted) -> Live (warm + in a game)
    -> Ready (warm, no game) -> Disconnected. "Warm" means the heartbeat
    (``last_seen_at``) is within ``_LIVE_WINDOW_SECONDS``; the displayed
    last-connected time falls back to ``first_connected_at`` so bots that
    connected before the heartbeat existed don't read as 'never connected'.
    """
    now = now or datetime.now(timezone.utc)
    last_seen = bot.last_seen_at
    warm = (
        last_seen is not None
        and (now - _as_aware(last_seen)).total_seconds() <= _LIVE_WINDOW_SECONDS
    )
    last_connected = bot.last_seen_at or bot.first_connected_at
    never = last_connected is None
    last_connected_aware = _as_aware(last_connected) if last_connected is not None else None
    human = None if last_connected is None else _humanize_since(last_connected, now)

    def build(
        state: BotHealth, *, game: Match | None = None, needs_reconnect: bool = False
    ) -> BotHealthStatus:
        label, css, pulse = _HEALTH_PRESENTATION[state]
        return BotHealthStatus(
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
        return build(BotHealth.PAUSED)

    games = (
        (
            await db.execute(
                select(Match)
                .join(Player, Player.match_id == Match.id)
                .where(Player.bot_id == bot.id, Player.left_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    active = next((g for g in games if g.state == GameState.ACTIVE), None)

    if active is not None:
        threshold = max(1, bot.stall_threshold)
        if not warm or await _is_defaulting(db, bot.id, active.id, threshold):
            return build(BotHealth.STALLED, game=active, needs_reconnect=True)
        return build(BotHealth.LIVE, game=active)

    if warm:
        return build(BotHealth.READY)
    return build(BotHealth.DISCONNECTED, needs_reconnect=True)
