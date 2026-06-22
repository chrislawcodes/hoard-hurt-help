"""Liar's Dice — match-state accessors.

Read/normalize helpers over the match `state_json` blob and the per-player
`state_json` dice, plus the small async loaders that fetch the rows. Pure
state-shaping logic lives here so `game.py` can stay focused on the
`GameModule` contract.

This module never imports `game.py` (it sits below it): it depends only on the
pure engine (`Bid`, `_next_alive_seat`) and the ORM models, keeping imports
acyclic.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.games.liars_dice.engine import Bid, _next_alive_seat
from app.models.game_state import MatchState, PlayerState
from app.models.match import Match
from app.models.player import Player

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "_alive_players",
    "_challenger_name",
    "_default_config",
    "_dice_count",
    "_dice_list",
    "_ensure_state_defaults",
    "_load_config",
    "_load_match",
    "_load_state",
    "_next_alive_seat",
    "_player_state_map",
    "_players",
    "_public_dice_counts",
    "_seat_order",
    "_standing_bid",
    "_state_template",
]


def _default_config() -> dict[str, Any]:
    return {"wild_ones": True, "dice_per_player": 5}


def _state_template() -> dict[str, Any]:
    return {
        "config": _default_config(),
        "hand": 0,
        "seat_order": [],
        "standing_bid": None,
        "active_actor": None,
        "challenge_pending": False,
        "challenger": None,
        "next_leader": None,
        "last_showdown": None,
        "showdowns": [],
        "bid_history": [],
        "elimination_order": [],
        "showdown_resolved_hand": 0,
    }


def _load_config(state_json: dict[str, Any]) -> dict[str, Any]:
    config = _default_config()
    raw = state_json.get("config")
    if isinstance(raw, dict):
        if isinstance(raw.get("wild_ones"), bool):
            config["wild_ones"] = raw["wild_ones"]
        dice = raw.get("dice_per_player")
        if isinstance(dice, int) and not isinstance(dice, bool) and dice > 0:
            config["dice_per_player"] = dice
    return config


def _ensure_state_defaults(state_json: dict[str, Any]) -> dict[str, Any]:
    template = _state_template()
    for key, value in template.items():
        if key not in state_json:
            state_json[key] = copy.deepcopy(value)
    state_json["config"] = _load_config(state_json)
    return state_json


async def _load_match(db: AsyncSession, match_id: str) -> Match:
    return (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()


async def _load_state(
    db: AsyncSession, match_id: str, *, create: bool = False
) -> MatchState | None:
    state = (
        await db.execute(select(MatchState).where(MatchState.match_id == match_id))
    ).scalar_one_or_none()
    if state is None and create:
        state = MatchState(match_id=match_id, state_json=_ensure_state_defaults(_state_template()))
        db.add(state)
        await db.flush()
    elif state is not None:
        _ensure_state_defaults(state.state_json)
    return state


async def _players(db: AsyncSession, match_id: str) -> list[Player]:
    return list(
        (
            await db.execute(
                select(Player).where(Player.match_id == match_id).order_by(Player.seat_name)
            )
        )
        .scalars()
        .all()
    )


async def _player_state_map(db: AsyncSession, match_id: str) -> dict[int, PlayerState]:
    rows = (
        (
            await db.execute(select(PlayerState).where(PlayerState.match_id == match_id))
        )
        .scalars()
        .all()
    )
    return {row.player_id: row for row in rows}


def _dice_count(state: PlayerState | None) -> int:
    if state is None:
        return 0
    count = state.state_json.get("dice_count")
    if isinstance(count, int) and not isinstance(count, bool):
        return max(0, count)
    dice = state.state_json.get("dice")
    if isinstance(dice, list):
        return sum(1 for die in dice if isinstance(die, int))
    return 0


def _dice_list(state: PlayerState | None) -> list[int]:
    if state is None:
        return []
    dice = state.state_json.get("dice")
    if not isinstance(dice, list):
        return []
    return [die for die in dice if isinstance(die, int)]


def _public_dice_counts(players: list[Player], states: dict[int, PlayerState]) -> dict[str, int]:
    return {
        player.seat_name: _dice_count(states.get(player.id))
        for player in players
        if player.left_at is None
    }


def _alive_players(players: list[Player], states: dict[int, PlayerState]) -> list[Player]:
    return [
        player
        for player in players
        if player.left_at is None and _dice_count(states.get(player.id)) > 0
    ]


def _seat_order(players: list[Player]) -> list[str]:
    return [player.seat_name for player in players]


def _standing_bid(raw: Any) -> Bid | None:
    if not isinstance(raw, dict):
        return None
    quantity = raw.get("quantity")
    face = raw.get("face")
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        return None
    if not isinstance(face, int) or isinstance(face, bool):
        return None
    return Bid(quantity=quantity, face=face)


def _challenger_name(state_json: dict[str, Any]) -> str | None:
    challenger = state_json.get("challenger")
    return challenger if isinstance(challenger, str) else None
