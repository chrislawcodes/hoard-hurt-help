"""Match state transitions.

Pure functions over GameState; the route layer wraps these with DB writes.
"""

from app.models.match import GameState

# Allowed transitions. Anything not in this map is forbidden.
ALLOWED: dict[GameState, set[GameState]] = {
    GameState.SCHEDULED: {GameState.REGISTERING, GameState.CANCELLED},
    GameState.REGISTERING: {GameState.ACTIVE, GameState.CANCELLED},
    GameState.ACTIVE: {GameState.COMPLETED},
    GameState.COMPLETED: set(),
    GameState.CANCELLED: set(),
}


class TransitionError(Exception):
    pass


def allowed_transitions(from_state: GameState) -> set[GameState]:
    return ALLOWED[from_state]


def assert_transition(from_state: GameState, to_state: GameState) -> None:
    if to_state not in ALLOWED[from_state]:
        raise TransitionError(f"Illegal transition {from_state.value} → {to_state.value}")
