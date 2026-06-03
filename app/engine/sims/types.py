"""Shared Sims dataclasses."""

from __future__ import annotations

from dataclasses import dataclass

from app.engine.game_records import ActionRecord
from app.schemas.agent import ScoreboardRow, TalkMessage


@dataclass(frozen=True)
class SimProfile:
    strategy: str
    truthfulness: int
    trust_model: str
    seed: int
    version: str
    fixture_pack: str | None = None


@dataclass(frozen=True)
class SimContext:
    game_id: str
    round: int
    turn: int
    phase: str
    your_agent_id: str
    all_agent_ids: list[str]
    history: list[ActionRecord]
    scoreboard: list[ScoreboardRow]
    current_talk_messages: list[TalkMessage]


@dataclass(frozen=True)
class SimPlan:
    intent: str
    target_id: str | None
    reason: str


@dataclass(frozen=True)
class SimTalkDecision:
    intent: str
    truth_mode: str
    message: str
    thinking: str


@dataclass(frozen=True)
class SimActionDecision:
    intent: str
    move: dict[str, str | None]
    thinking: str

