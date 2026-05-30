"""Pure selection of a bot's single most-urgent open turn across its games.

Kept DB-free so it is unit-testable. The endpoint (app/routes/agent_next_turn.py)
gathers the candidate open turns the bot still owes a move on — already-submitted
turns are excluded there — then calls this to pick which one to hand back.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TurnCandidate:
    """One open turn the bot still needs to act on, in one of its active games."""

    game_id: str
    round: int
    turn: int
    deadline: datetime


def select_next_turn(candidates: Sequence[TurnCandidate]) -> TurnCandidate | None:
    """Return the most urgent candidate, or None when there are none.

    Most urgent = nearest deadline. Ties break deterministically by game_id,
    then round, then turn, so the play loop is predictable and testable.
    """
    if not candidates:
        return None
    return min(candidates, key=lambda c: (c.deadline, c.game_id, c.round, c.turn))
