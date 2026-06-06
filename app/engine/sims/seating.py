"""Seat platform bots into a game as ready-to-play players.

The admin's "Add Sims" screen posts a roster of ``(name, personality)`` rows;
this module validates them and creates, for each row, a backing bot agent plus a
player. Bots are owned by a single internal "Platform Bots" user so they never
clutter a human's agent list, and they carry no usable credential - the
scheduler drives them directly (see :mod:`app.engine.sims.service`).

A separate bot per seat is required: a player is uniquely keyed to one bot per
game, and the deterministic runtime reads each player's traits and seed off its
bot agent, so two bots of the same personality need two agents to play (and
vary) independently.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.sim_presets import sim_preset_by_id
from app.engine.sims.roster import is_known_personality
from app.models.agent import Agent, AgentKind
from app.models.match import Match
from app.models.player import Player
from app.models.user import User

# Platform bot display names may include spaces so historical names like
# "Sun Tzu" do not have to be flattened.
BOT_AGENT_NAME_RE = re.compile(r"[A-Za-z0-9 ]{1,32}")

# The internal owner of every platform bot. Not a real Google identity, so the
# sentinel sub never collides with a signed-in user.
BOTS_USER_SUB = "platform:bots"
BOTS_USER_EMAIL = "bots@agentludum.local"
BOTS_USER_NAME = "Platform Bots"

SIM_AGENT_NAME_RE = BOT_AGENT_NAME_RE
SIMS_USER_SUB = BOTS_USER_SUB
SIMS_USER_EMAIL = BOTS_USER_EMAIL
SIMS_USER_NAME = BOTS_USER_NAME


class SimSeatingError(Exception):
    """A roster the admin submitted can't be seated; message is user-facing."""


async def get_or_create_bots_user(db: AsyncSession) -> User:
    """The single internal user that owns all platform bots."""
    user = (
        await db.execute(select(User).where(User.google_sub == BOTS_USER_SUB))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            google_sub=BOTS_USER_SUB,
            email=BOTS_USER_EMAIL,
            name=BOTS_USER_NAME,
        )
        db.add(user)
        await db.flush()
    return user


async def _existing_seat_names(db: AsyncSession, match_id: str) -> list[str]:
    return list(
        (
            await db.execute(
                select(Player.seat_name).where(
                    Player.match_id == match_id, Player.left_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )


def _validate_roster(
    seats: list[tuple[str, str]], existing: set[str], max_players: int
) -> None:
    if not seats:
        raise SimSeatingError("Add at least one bot to save.")
    seen: set[str] = set()
    for name, strategy in seats:
        if not is_known_personality(strategy):
            raise SimSeatingError(f"Unknown personality: {strategy!r}.")
        if not SIM_AGENT_NAME_RE.fullmatch(name):
            raise SimSeatingError(
                f"“{name}” isn’t a valid name. Use letters, numbers, or spaces "
                "(up to 32)."
            )
        if name in existing:
            raise SimSeatingError(f"“{name}” is already taken in this game.")
        if name in seen:
            raise SimSeatingError(f"“{name}” is listed twice.")
        seen.add(name)
    total = len(existing) + len(seats)
    if total > max_players:
        raise SimSeatingError(
            f"That would seat {total} players, over the {max_players} cap. "
            "Remove a few bots."
        )


async def add_bots_to_game(
    db: AsyncSession, game: Match, seats: list[tuple[str, str]]
) -> list[Player]:
    """Validate ``seats`` and seat each as a bot player. Commits on success.

    ``seats`` is a list of ``(name, personality_id)``. Raises
    :class:`SimSeatingError` (no commit) if any name is invalid, duplicated,
    already taken, the personality is unknown, or the table would overflow.
    """
    existing = set(await _existing_seat_names(db, game.id))
    _validate_roster(seats, existing, game.max_players)

    bots_user = await get_or_create_bots_user(db)
    created: list[Player] = []
    for name, strategy in seats:
        preset = sim_preset_by_id(strategy)
        if preset is None:  # guarded by _validate_roster, kept for type-safety
            raise SimSeatingError(f"Unknown personality: {strategy!r}.")
        agent = Agent(
            user_id=bots_user.id,
            name=f"{game.id}:{name}",
            kind=AgentKind.BOT,
            game=game.game,
            bot_profile_name=preset.name,
            bot_strategy=preset.strategy,
            bot_truthfulness=preset.truthfulness,
            bot_trust_model=preset.trust_model,
            bot_version="v1",
        )
        db.add(agent)
        await db.flush()
        # agent.id is globally unique, so each seat gets a distinct seed - same
        # personality, different tie-breaks and wording.
        agent.bot_seed = agent.id
        player = Player(
            match_id=game.id,
            user_id=bots_user.id,
            agent_id=agent.id,
            seat_name=name,
        )
        db.add(player)
        await db.flush()
        created.append(player)

    await db.commit()
    return created


async def get_or_create_sims_user(db: AsyncSession) -> User:
    return await get_or_create_bots_user(db)


async def add_sims_to_game(
    db: AsyncSession, game: Match, seats: list[tuple[str, str]]
) -> list[Player]:
    return await add_bots_to_game(db, game, seats)
