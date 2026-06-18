"""Prisoner's Dilemma — `hoard-hurt-help`.

It implements the `GameModule` contract using this module's own PD rules and
scoring (`app.games.hoard_hurt_help.rules` / `.scoring`) and delegating the
game-agnostic talk/round/game finalization to `app.engine.resolver`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.agent_prompt import make_agent_base_prompt
from app.engine import resolver
from app.games.base import (
    BaseGameModule,
    GameConfig,
    GameError,
    GameTheme,
    StrategyPreset,
)
from app.games.hoard_hurt_help import scoring
from app.games.hoard_hurt_help.rules import (
    HELP_POINTS,
    HOARD_POINTS,
    HURT_POINTS,
    make_game_rules_text,
    make_rules_text,
)
from app.games.hoard_hurt_help.strategy import PD_DEFAULT_STRATEGY, PD_STRATEGY_PRESETS
from app.models.player import Player
from app.models.turn import TurnMessage, TurnSubmission

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.match import Match
    from app.models.turn import Turn
    from app.read_models.matches import TimelineTurn

_VALID_ACTIONS = {"HOARD", "HELP", "HURT"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HoardHurtHelp(BaseGameModule):
    """The Prisoner's Dilemma game module."""

    game_type = "hoard-hurt-help"

    def display_name(self) -> str:
        return "Hoard · Hurt · Help"

    def tagline(self) -> str:
        return "A multiplayer game of trust and betrayal for AI agents."

    def config_defaults(self) -> GameConfig:
        return GameConfig(
            total_rounds=7,
            turns_per_round=7,
            per_turn_deadline_seconds=60,
            min_players=6,
            max_players=100,
        )

    def action_names(self) -> tuple[str, ...]:
        # Canonical display order the insight engines tally moves in:
        # HOARD (keep), HELP (cooperate), HURT (attack).
        return ("HOARD", "HELP", "HURT")

    def rules_text(self, total_rounds: int = 7, turns_per_round: int = 7) -> str:
        return make_rules_text(total_rounds, turns_per_round)

    def semantic_rules_text(self, total_rounds: int = 7, turns_per_round: int = 7) -> str:
        return make_game_rules_text(total_rounds, turns_per_round)

    def strategy_presets(self) -> list[StrategyPreset]:
        return PD_STRATEGY_PRESETS

    def default_strategy(self) -> str:
        return PD_DEFAULT_STRATEGY

    def agent_base_prompt(
        self,
        *,
        your_agent_id: str,
        all_agent_ids: list[str],
        total_rounds: int = 7,
        turns_per_round: int = 7,
    ) -> str:
        return make_agent_base_prompt(
            your_agent_id=your_agent_id,
            all_agent_ids=all_agent_ids,
            rules=make_game_rules_text(total_rounds, turns_per_round),
        )

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
        is_connector_fallback: bool = False,
    ) -> None:
        action = str(move["action"]).upper()
        target_id = move.get("target_id")
        target_player_id: int | None = None
        if target_id is not None:
            target = (
                await db.execute(
                    select(Player).where(
                        Player.match_id == turn.match_id, Player.agent_id == target_id
                    )
                )
            ).scalar_one_or_none()
            target_player_id = target.id if target is not None else None
        message = str(move.get("message", ""))
        thinking = str(move.get("thinking", ""))
        # Connector fallbacks reuse the existing was_defaulted column so they are
        # identifiable in the DB without a migration. A genuine move clears the flag.
        was_defaulted = is_connector_fallback
        if existing is not None:
            existing.action = action
            existing.target_player_id = target_player_id
            existing.message = message
            existing.thinking = thinking
            existing.was_defaulted = was_defaulted
            existing.submitted_at = _now()
        else:
            db.add(
                TurnSubmission(
                    turn_id=turn.id,
                    player_id=player.id,
                    action=action,
                    target_player_id=target_player_id,
                    message=message,
                    thinking=thinking,
                    was_defaulted=was_defaulted,
                    submitted_at=_now(),
                )
            )

    async def record_message(
        self,
        db: AsyncSession,
        turn: Turn,
        player: Player,
        message: str,
        thinking: str,
        *,
        existing: TurnMessage | None,
        is_connector_fallback: bool = False,
    ) -> None:
        # Connector fallbacks reuse the existing was_defaulted column.
        was_defaulted = is_connector_fallback
        if existing is not None:
            existing.text = message
            existing.thinking = thinking
            existing.was_defaulted = was_defaulted
            existing.submitted_at = _now()
        else:
            db.add(
                TurnMessage(
                    turn_id=turn.id,
                    player_id=player.id,
                    text=message,
                    thinking=thinking,
                    was_defaulted=was_defaulted,
                    submitted_at=_now(),
                )
            )

    async def resolve_turn(self, db: AsyncSession, turn: Turn) -> None:
        await scoring.resolve_turn(db, turn)

    async def award_round(self, db: AsyncSession, game: Match, round_num: int) -> None:
        await resolver.award_round_winners(db, game, round_num)

    async def finalize(self, db: AsyncSession, game: Match) -> None:
        await resolver.finalize_game(db, game)

    async def default_move(
        self, db: AsyncSession, match: Match, player: Player
    ) -> dict[str, Any]:
        # A missed deadline records HOARD (keep, target nobody) — PD's long-standing
        # default move, made explicit now that the base no longer assumes it.
        return {"action": "HOARD", "target_id": None}

    def move_effect(self, action: str) -> tuple[int, int | None]:
        a = action.upper()
        if a == "HOARD":
            return HOARD_POINTS, None
        if a == "HELP":
            return 0, HELP_POINTS
        if a == "HURT":
            return 0, -HURT_POINTS
        return 0, None

    async def build_replay_view(
        self,
        db: AsyncSession,
        match: Match,
        players: list[Player],
        scoreboard: list[dict[str, Any]],
        timeline: list[TimelineTurn],
        viewer_seat: str | None,
    ) -> dict[str, Any]:
        from app.games.hoard_hurt_help.viewer import build_pd_replay_view

        return await build_pd_replay_view(
            db, match, players, scoreboard, timeline, viewer_seat
        )

    def viewer_fragment(self) -> str:
        return "fragments/pd_live_region.html"

    def theme(self) -> GameTheme:
        # The flagship game wears the platform's warm orange, plus the move trio
        # (hoard amber / help green / hurt red) as its semantic colors and a
        # faintly warm surface so its pages read as "this game" inside the shared
        # Agent Ludum shell. Only content tokens here — never chrome.
        return GameTheme(
            key=self.game_type,
            vars={
                "--brand": "#e2640e",
                "--brand-2": "#5b4fd6",
                "--accent": "#b8861a",
                "--on-brand": "#fff6ec",
                "--surface": "#fbf7f1",
                "--surface-2": "#f3ece1",
                "--hoard": "#b07e0d",
                "--help": "#1f8a5b",
                "--hurt": "#c1452f",
            },
        )
