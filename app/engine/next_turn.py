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

    match_id: str
    round: int
    turn: int
    deadline: datetime
    agent_id: int = 0


def select_next_turn(candidates: Sequence[TurnCandidate]) -> TurnCandidate | None:
    """Return the most urgent candidate, or None when there are none.

    Most urgent = nearest deadline. Ties break deterministically by match_id,
    then round, then turn, then agent_id, so the play loop is predictable and
    testable even when one connection fields multiple agents in the same match.
    """
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda c: (c.deadline, c.match_id, c.round, c.turn, c.agent_id),
    )
