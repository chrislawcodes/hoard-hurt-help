"""Seat platform Sims into a game as ready-to-play players.

The admin's "Add Sims" screen posts a roster of ``(name, personality)`` rows;
this module validates them and creates, for each row, a backing Sim bot plus a
player. Sims are owned by a single internal "Platform Sims" user so they never
clutter a human's bot list, and they carry no usable credential — the scheduler
drives them directly (see :mod:`app.engine.sims.service`).

A separate bot per seat is required: a player is uniquely keyed to one bot per
game (``UNIQUE(bot_id, match_id)``), and the Sim runtime reads each player's
traits and seed off its bot, so two Sims of the same personality need two bots
to play (and vary) independently.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.sim_presets import SimPreset, sim_preset_by_id
from app.engine.sims.roster import is_known_personality
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_bot_key
from app.models.bot import Bot, BotKind
from app.models.match import Match
from app.models.player import Player
from app.models.strategy_prompt import StrategyPrompt
from app.models.user import User

# Platform Sim display names may include spaces so historical names like
# "Sun Tzu" do not have to be flattened.
SIM_AGENT_NAME_RE = re.compile(r"[A-Za-z0-9 ]{1,32}")

# The internal owner of every platform Sim. Not a real Google identity, so the
# sentinel sub never collides with a signed-in user.
SIMS_USER_SUB = "platform:sims"
SIMS_USER_EMAIL = "sims@agentludum.local"
SIMS_USER_NAME = "Platform Sims"


class SimSeatingError(Exception):
    """A roster the admin submitted can't be seated; message is user-facing."""


async def get_or_create_sims_user(db: AsyncSession) -> User:
    """The single internal user that owns all platform Sims."""
    user = (
        await db.execute(select(User).where(User.google_sub == SIMS_USER_SUB))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            google_sub=SIMS_USER_SUB,
            email=SIMS_USER_EMAIL,
            name=SIMS_USER_NAME,
        )
        db.add(user)
        await db.flush()
    return user


async def _existing_agent_ids(db: AsyncSession, match_id: str) -> list[str]:
    return list(
        (
            await db.execute(
                select(Player.agent_id).where(
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
        raise SimSeatingError("Add at least one Sim to save.")
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
            "Remove a few Sims."
        )


def _sim_prompt_text(preset: SimPreset) -> str:
    """A short, human-readable note stored as the Sim's strategy prompt, so the
    admin prompts view and exports label the Sim instead of showing a blank."""
    return (
        f"[Sim] {preset.name} — deterministic platform bot. "
        f"Strategy {preset.strategy}, truthfulness {preset.truthfulness}%, "
        f"{preset.trust_model} trust."
    )


async def add_sims_to_game(
    db: AsyncSession, game: Match, seats: list[tuple[str, str]]
) -> list[Player]:
    """Validate ``seats`` and seat each as a Sim player. Commits on success.

    ``seats`` is a list of ``(name, personality_id)``. Raises
    :class:`SimSeatingError` (no commit) if any name is invalid, duplicated,
    already taken, the personality is unknown, or the table would overflow.
    """
    existing = set(await _existing_agent_ids(db, game.id))
    _validate_roster(seats, existing, game.max_players)

    sims_user = await get_or_create_sims_user(db)
    created: list[Player] = []
    for name, strategy in seats:
        preset = sim_preset_by_id(strategy)
        if preset is None:  # guarded by _validate_roster, kept for type-safety
            raise SimSeatingError(f"Unknown personality: {strategy!r}.")
        key = generate_bot_key()
        bot = Bot(
            user_id=sims_user.id,
            # Unique per (game, agent) and so unique under this one owner.
            name=f"{game.id}:{name}",
            key_lookup=bot_key_lookup(key),
            key_hint=bot_key_hint(key),
            kind=BotKind.SIM,
            sim_profile_name=preset.name,
            sim_strategy=preset.strategy,
            sim_truthfulness=preset.truthfulness,
            sim_trust_model=preset.trust_model,
            sim_version="v1",
        )
        db.add(bot)
        await db.flush()
        # bot.id is globally unique, so each seat gets a distinct seed — same
        # personality, different tie-breaks and wording.
        bot.sim_seed = bot.id
        player = Player(
            match_id=game.id,
            user_id=sims_user.id,
            bot_id=bot.id,
            agent_id=name,
        )
        db.add(player)
        await db.flush()
        db.add(
            StrategyPrompt(
                player_id=player.id,
                prompt_text=_sim_prompt_text(preset),
                is_default=False,
            )
        )
        created.append(player)

    await db.commit()
    return created
