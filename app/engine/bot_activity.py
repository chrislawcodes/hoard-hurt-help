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
from app.models.bot import Bot
from app.models.game import Game, GameState
from app.models.player import Player
from app.models.turn import TurnSubmission

_PREGAME_STATES = (GameState.SCHEDULED, GameState.REGISTERING)


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
    game_id: str | None = None
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


async def mark_connected(db: AsyncSession, bot: Bot) -> None:
    """Record the bot's first successful authenticated call and announce it.

    Idempotent: writes and publishes only on the ``NULL -> now`` transition, so
    every later agent call is a no-op here. Called from the single auth choke
    point (``require_bot``), which is why it covers every connection method
    (runner, MCP, direct API) with one hook.
    """
    if bot.first_connected_at is not None:
        return
    bot.first_connected_at = datetime.now(timezone.utc)
    await db.commit()
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
    takes precedence so established bots (including any created before this
    feature, with a NULL ``first_connected_at``) render the quiet "playing" state.
    """
    games = (
        (
            await db.execute(
                select(Game)
                .join(Player, Player.game_id == Game.id)
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
        watch = active or (games[0] if games else None)
        return OnboardingStatus(
            OnboardingState.PLAYING,
            bot_name=bot.name,
            game_id=watch.id if watch else None,
            game_name=watch.name if watch else None,
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
