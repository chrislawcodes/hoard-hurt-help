"""Game state transitions."""

import pytest

from app.engine.state_machine import (
    TransitionError,
    allowed_transitions,
    assert_transition,
)
from app.models.game import GameState


@pytest.mark.parametrize(
    "frm,to,expected_ok",
    [
        (GameState.SCHEDULED, GameState.REGISTERING, True),
        (GameState.SCHEDULED, GameState.CANCELLED, True),
        (GameState.SCHEDULED, GameState.ACTIVE, False),
        (GameState.REGISTERING, GameState.ACTIVE, True),
        (GameState.REGISTERING, GameState.CANCELLED, True),
        (GameState.REGISTERING, GameState.COMPLETED, False),
        (GameState.ACTIVE, GameState.COMPLETED, True),
        (GameState.ACTIVE, GameState.CANCELLED, False),  # not in v1
        (GameState.COMPLETED, GameState.ACTIVE, False),
        (GameState.CANCELLED, GameState.ACTIVE, False),
    ],
)
def test_transitions(frm, to, expected_ok):
    if expected_ok:
        assert_transition(frm, to)
    else:
        with pytest.raises(TransitionError):
            assert_transition(frm, to)


def test_allowed_transitions_complete():
    """Every state has an entry in ALLOWED, even if empty."""
    for state in GameState:
        allowed_transitions(state)  # does not raise
