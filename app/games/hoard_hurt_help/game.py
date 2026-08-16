"""Prisoner's Dilemma — `hoard-hurt-help`.

It implements the `GameModule` contract using this module's own PD rules and
scoring (`app.games.hoard_hurt_help.rules` / `.scoring`) and delegating the
game-agnostic talk/round/game finalization to `app.engine.resolver`.
"""

from __future__ import annotations

import os
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
    MUTUAL_HELP_FLOOR,
    make_game_rules_text,
    make_rules_text,
    mode_needs_history,
    mutual_help_value,
)
from app.games.hoard_hurt_help.strategy import PD_DEFAULT_STRATEGY, PD_STRATEGY_PRESETS
from app.models.player import Player
from app.models.turn import TurnMessage, TurnSubmission

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.engine.game_insights import RoundDetail, SeasonOverview
    from app.engine.game_records import ActionRecord, PlayerRecord
    from app.models.match import Match
    from app.models.turn import Turn
    from app.read_models.matches import TimelineTurn
    from app.schemas.agent import BoardSignals

_VALID_ACTIONS = {"HOARD", "HELP", "HURT"}

# Act-phase window for new matches. Reasoning models (e.g. gpt-5.4-mini) can take
# ~50s to decide a move; 75s clears them with margin.
DEFAULT_ACT_DEADLINE_SECONDS = 75


def act_deadline_seconds() -> int:
    """The act-phase window a new match starts with.

    Overridable via HHH_ACT_DEADLINE_SECONDS so an experiment can widen the window
    without changing the game every real player sees. The talk phase has its own knob,
    HHH_TALK_DEADLINE_SECONDS (see app/engine/scheduler_turn_loop.py), and the two are
    NOT independent: the talk window is min(per_turn_deadline_seconds, that cap), so
    widening talk alone does nothing until this one is widened to match.

    Read per call rather than at import, so tests can vary it without reloading this
    module — a reload would swap the HoardHurtHelp class out from under the game
    registry. Note a change still only reaches matches created afterwards, since each
    match stores its own deadline at creation time.
    """
    return int(os.environ.get("HHH_ACT_DEADLINE_SECONDS", DEFAULT_ACT_DEADLINE_SECONDS))


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
            total_rounds=5,
            turns_per_round=7,
            per_turn_deadline_seconds=act_deadline_seconds(),
            min_players=6,
            max_players=10,
        )

    def action_names(self) -> tuple[str, ...]:
        # Canonical display order the insight engines tally moves in:
        # HOARD (keep), HELP (cooperate), HURT (attack).
        return ("HOARD", "HELP", "HURT")

    def rules_text(
        self, total_rounds: int = 5, turns_per_round: int = 7, *, mutual_help_mode: str = "decay"
    ) -> str:
        return make_rules_text(total_rounds, turns_per_round, mode=mutual_help_mode)

    def semantic_rules_text(
        self, total_rounds: int = 5, turns_per_round: int = 7, *, mutual_help_mode: str = "decay"
    ) -> str:
        return make_game_rules_text(
            total_rounds, turns_per_round, mode=mutual_help_mode
        )

    def strategy_presets(self) -> list[StrategyPreset]:
        return PD_STRATEGY_PRESETS

    def default_strategy(self) -> str:
        return PD_DEFAULT_STRATEGY

    def agent_base_prompt(
        self,
        *,
        your_agent_id: str,
        all_agent_ids: list[str],
        total_rounds: int = 5,
        turns_per_round: int = 7,
        mutual_help_mode: str = "decay",
    ) -> str:
        return make_agent_base_prompt(
            your_agent_id=your_agent_id,
            all_agent_ids=all_agent_ids,
            rules=make_game_rules_text(
                total_rounds, turns_per_round, mode=mutual_help_mode
            ),
        )

    # Per-match overrides: PD's rules vary with the match's `mutual_help_mode`
    # switch, so the platform's match-aware callers get the setting-correct text.
    # `is not False` treats a legacy/unflushed None as the ON default.
    def rules_text_for_match(self, match: Match) -> str:
        return self.rules_text(
            match.total_rounds,
            match.turns_per_round,
            mutual_help_mode=match.mutual_help_mode or "decay",
        )

    def semantic_rules_text_for_match(self, match: Match) -> str:
        return self.semantic_rules_text(
            match.total_rounds,
            match.turns_per_round,
            mutual_help_mode=match.mutual_help_mode or "decay",
        )

    def agent_base_prompt_for_match(
        self, match: Match, *, your_agent_id: str, all_agent_ids: list[str]
    ) -> str:
        return self.agent_base_prompt(
            your_agent_id=your_agent_id,
            all_agent_ids=all_agent_ids,
            total_rounds=match.total_rounds,
            turns_per_round=match.turns_per_round,
            mutual_help_mode=match.mutual_help_mode or "decay",
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

    async def private_state_for(
        self, db: AsyncSession, match: Match, player: Player
    ) -> dict[str, Any]:
        # `pact_values`: what a mutual HELP with each other seat would pay EACH
        # side RIGHT NOW — already decayed by that pair's prior mutual helps this
        # match. Lets an agent read the current per-pair decay counter `k` off
        # the payload instead of re-scanning full match history to recount it
        # (feature `mutual-help-pact-value`; k itself comes from
        # `scoring.mutual_help_counts`, derived from resolved turns so it's
        # resume-safe).
        all_players = (
            (await db.execute(select(Player).where(Player.match_id == match.id)))
            .scalars()
            .all()
        )
        other_players = [p for p in all_players if p.id != player.id]
        if not other_players:
            return {}
        mode = match.mutual_help_mode or "decay"
        values = await scoring.current_pact_values(
            db, match.id, player.id, (p.id for p in other_players), mode=mode
        )
        if mode_needs_history(mode):
            note = (
                "What a mutual HELP with this agent would pay EACH side right "
                "now (decays per repeat mutual-help pair this match; floors at "
                f"{MUTUAL_HELP_FLOOR})."
            )
        else:
            note = (
                "What a mutual HELP with this agent pays EACH side: a flat "
                f"+{mutual_help_value(mode, 0)}, every time."
            )
        return {
            "pact_values": {p.seat_name: values[p.id] for p in other_players},
            "pact_values_note": note,
        }

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

    def board_signals(
        self,
        players: Sequence[PlayerRecord],
        actions: Sequence[ActionRecord],
        current_round: int,
    ) -> BoardSignals:
        from app.games.hoard_hurt_help.board_signals import compute_board_signals

        return compute_board_signals(players, actions, current_round)

    def season_overview(
        self,
        players: Sequence[PlayerRecord],
        actions: Sequence[ActionRecord],
        total_rounds: int,
        current_round: int,
        game_active: bool,
    ) -> SeasonOverview:
        from app.games.hoard_hurt_help.insights import season_overview

        return season_overview(players, actions, total_rounds, current_round, game_active)

    def round_detail(
        self,
        round_num: int,
        players: Sequence[PlayerRecord],
        actions: Sequence[ActionRecord],
    ) -> RoundDetail:
        from app.games.hoard_hurt_help.insights import round_detail

        return round_detail(round_num, players, actions)

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
