"""Prisoner's Dilemma — `hoard-hurt-help`.

A thin adapter over the existing engine in `app/engine/*` (which is left
UNCHANGED, so its tests pass identically). It implements the `GameModule`
contract by delegating to `app.engine.resolver` and reusing the PD rules/scoring.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.engine import resolver
from app.engine.rules import HELP_POINTS, HOARD_POINTS, HURT_POINTS, RULES_TEXT_V1
from app.games.base import GameConfig, GameError, StrategyPreset
from app.games.hoard_hurt_help.strategy import PD_DEFAULT_STRATEGY, PD_STRATEGY_PRESETS
from app.models.player import Player
from app.models.turn import TurnSubmission

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.game import Game
    from app.models.turn import Turn

_VALID_ACTIONS = {"HOARD", "HELP", "HURT"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HoardHurtHelp:
    """The Prisoner's Dilemma game module."""

    game_type = "hoard-hurt-help"

    def config_defaults(self) -> GameConfig:
        return GameConfig(
            total_rounds=10,
            turns_per_round=10,
            per_turn_deadline_seconds=60,
            min_players=3,
            max_players=100,
        )

    def rules_text(self) -> str:
        return RULES_TEXT_V1

    def strategy_presets(self) -> list[StrategyPreset]:
        return PD_STRATEGY_PRESETS

    def default_strategy(self) -> str:
        return PD_DEFAULT_STRATEGY

    def validate_move(
        self, move: dict[str, Any], *, your_agent_id: str, all_agent_ids: list[str]
    ) -> None:
        action = str(move.get("action", "")).upper()
        target = move.get("target_id")
        if action not in _VALID_ACTIONS:
            raise GameError("INVALID_ACTION", "action must be HOARD, HELP, or HURT.")
        if action == "HOARD":
            if target is not None:
                raise GameError(
                    "TARGET_NOT_ALLOWED_FOR_HOARD", "HOARD must not have a target."
                )
            return
        if target is None:
            raise GameError("MISSING_TARGET", "HELP/HURT requires target_id.")
        if target == your_agent_id:
            raise GameError(
                "INVALID_TARGET", "Cannot target self.", {"reason": "self_target"}
            )
        if target not in all_agent_ids:
            raise GameError(
                "INVALID_TARGET",
                "Target not in this game.",
                {"reason": "unknown_agent"},
            )

    async def record_submission(
        self,
        db: AsyncSession,
        turn: Turn,
        player: Player,
        move: dict[str, Any],
        *,
        existing: TurnSubmission | None,
    ) -> None:
        action = str(move["action"]).upper()
        target_id = move.get("target_id")
        target_player_id: int | None = None
        if target_id is not None:
            target = (
                await db.execute(
                    select(Player).where(
                        Player.game_id == turn.game_id, Player.agent_id == target_id
                    )
                )
            ).scalar_one_or_none()
            target_player_id = target.id if target is not None else None
        message = str(move.get("message", ""))
        if existing is not None:
            existing.action = action
            existing.target_player_id = target_player_id
            existing.message = message
            existing.was_defaulted = False
            existing.submitted_at = _now()
        else:
            db.add(
                TurnSubmission(
                    turn_id=turn.id,
                    player_id=player.id,
                    action=action,
                    target_player_id=target_player_id,
                    message=message,
                    submitted_at=_now(),
                )
            )

    async def resolve_turn(self, db: AsyncSession, turn: Turn) -> None:
        await resolver.resolve_turn(db, turn)

    async def award_round(self, db: AsyncSession, game: Game, round_num: int) -> None:
        await resolver.award_round_winners(db, game, round_num)

    async def finalize(self, db: AsyncSession, game: Game) -> None:
        await resolver.finalize_game(db, game)

    def move_effect(self, action: str) -> tuple[int, int | None]:
        a = action.upper()
        if a == "HOARD":
            return HOARD_POINTS, None
        if a == "HELP":
            return 0, HELP_POINTS
        if a == "HURT":
            return 0, -HURT_POINTS
        return 0, None
