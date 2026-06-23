"""Shared bot dataclasses."""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass

from app.engine.game_records import ActionRecord
from app.schemas.agent import ScoreboardRow, TalkMessage


@dataclass(frozen=True)
class BotProfile:
    strategy: str
    truthfulness: int
    trust_model: str
    seed: int
    version: str
    fixture_pack: str | None = None


@dataclass(frozen=True)
class BotContext:
    # Kept for compatibility with the rest of the bot DTO plumbing, but it is no
    # longer part of the seed. The deterministic seed now comes from
    # `game_started_at` plus a canonicalized context snapshot so match IDs and
    # list ordering cannot perturb bot behavior.
    game_id: str
    game_started_at: datetime
    round: int
    turn: int
    phase: str
    your_agent_id: str
    all_agent_ids: list[str]
    history: list[ActionRecord]
    scoreboard: list[ScoreboardRow]
    current_talk_messages: list[TalkMessage]

    def seed_basis(self) -> str:
        """Canonical seed input: start time plus a sorted context snapshot.

        This turn's talk is deliberately excluded. The seed only breaks ties
        between equally-good options, and the talk phase sees no talk yet while
        the act phase sees all of it — so including talk would re-roll a bot's
        tie-breaks between the two phases, making it name one player but hit
        another. Talk still influences the move, but through signals → trust,
        not by perturbing the random seed.
        """
        history_bits = [
            "|".join(
                [
                    str(record.round),
                    str(record.turn),
                    record.actor_id,
                    record.action,
                    record.target_id or "",
                    record.message,
                    str(record.points_delta),
                    str(record.round_score_after),
                    "1" if record.was_defaulted else "0",
                ]
            )
            for record in sorted(
                self.history,
                key=lambda record: (
                    record.round,
                    record.turn,
                    record.actor_id,
                    record.action,
                    record.target_id or "",
                    record.message,
                    record.points_delta,
                    record.round_score_after,
                    record.was_defaulted,
                ),
            )
        ]
        scoreboard_bits = [
            "|".join(
                [
                    row.agent_id,
                    str(row.round_score),
                    str(row.round_wins),
                ]
            )
            for row in sorted(self.scoreboard, key=lambda row: row.agent_id)
        ]
        return "||".join(
            [
                self.game_started_at.isoformat(),
                str(self.round),
                str(self.turn),
                self.phase,
                self.your_agent_id,
                ",".join(sorted(self.all_agent_ids)),
                "#".join(history_bits),
                "#".join(scoreboard_bits),
            ]
        )


@dataclass(frozen=True)
class BotPlan:
    intent: str
    target_id: str | None
    reason: str


@dataclass(frozen=True)
class BotTalkDecision:
    intent: str
    truth_mode: str
    message: str
    thinking: str


@dataclass(frozen=True)
class BotActionDecision:
    intent: str
    move: dict[str, str | None]
    thinking: str
