"""Plain, DB-free input records the summary engine consumes.

The agent route maps DB rows (Player, Turn, TurnSubmission) into these records,
then hands them to the pure functions in `opponent_stats`, `board_signals`, and
`turn_summary`. Keeping the engine free of the database makes every function a
pure function over data — trivially unit-testable without a live DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Action = Literal["HOARD", "HELP", "HURT"]


@dataclass(frozen=True)
class PlayerRecord:
    """An active player's standing, identified by agent_id (not DB id)."""

    agent_id: str
    round_score: int
    total_score: int
    round_wins: float


@dataclass(frozen=True)
class ActionRecord:
    """One resolved submission, with agent ids already resolved from DB ids."""

    round: int
    turn: int
    actor_id: str
    action: Action
    target_id: str | None
    message: str
    points_delta: int
    round_score_after: int
    was_defaulted: bool
