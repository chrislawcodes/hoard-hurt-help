"""The field-only match-cancel transition.

`cancel_match` and the scheduler/arena inline cancel paths all set the same two
fields when cancelling a match. This is that write, once. It is deliberately
*field-only*: it takes `now` as a parameter (so each caller keeps its own
fresh-or-captured timestamp) and does NOT commit, log, or stop the scheduler
registry — those side effects stay with each caller (`cancel_match` keeps
`registry.stop`; the inline sites keep their own commit/logging). Leaf module
(imports only the `Match` model), so it is cycle-free for `scheduler.py`,
`arena.py`, and `match_deletion.py` alike.
"""
from __future__ import annotations

from datetime import datetime

from app.models.match import GameState, Match


def mark_cancelled(match: Match, now: datetime) -> None:
    """Mark *match* cancelled: set ``state=CANCELLED`` and ``cancelled_at=now``."""
    match.state = GameState.CANCELLED
    match.cancelled_at = now
