"""The named match-state groups, and the partition that keeps them honest.

`FINISHED_STATES` / `UNFINISHED_STATES` / `NOT_STARTED_STATES` replaced 12
hand-written literals spread across engine/, read_models/ and routes/. Two of
those sites expressed the SAME rule in opposite directions -- three used
`in_(SCHEDULED, REGISTERING, ACTIVE)` while `join_gate_capacity` used
`notin_(COMPLETED, CANCELLED)`. Correct today, and guaranteed to disagree the
first time a sixth state is added, because one form opts a new state IN and the
other opts it OUT.

Naming the sets removes the drift only if the sets stay a true partition, which
is what this file pins. The groups are deliberately spelled out rather than
derived from one another: a derived complement would absorb a new state silently
and choose a behaviour nobody decided. Written out, adding a state breaks these
tests until a human classifies it.
"""

from __future__ import annotations

from app.models.match import (
    FINISHED_STATES,
    NOT_STARTED_STATES,
    UNFINISHED_STATES,
    GameState,
)


def test_finished_and_unfinished_partition_every_state() -> None:
    """Every state is on exactly one side of the finished line.

    Fails when a new GameState member is added, which is the point: the person
    adding it has to say whether it counts as finished. Silence would leave it
    matching neither group and quietly disappearing from both sets of queries.
    """
    assert set(FINISHED_STATES) | set(UNFINISHED_STATES) == set(GameState), (
        "a GameState member is in neither FINISHED_STATES nor UNFINISHED_STATES; "
        "classify it in app/models/match.py"
    )
    assert not set(FINISHED_STATES) & set(UNFINISHED_STATES)


def test_not_started_is_a_subset_of_unfinished() -> None:
    """A match that has not started cannot already be finished."""
    assert set(NOT_STARTED_STATES) < set(UNFINISHED_STATES)


def test_groups_hold_the_states_the_queries_expect() -> None:
    """Pin the actual membership, not just the shape.

    Without this, someone could satisfy the partition by moving ACTIVE into
    FINISHED_STATES and every query above would flip meaning while staying green.
    """
    assert set(FINISHED_STATES) == {GameState.COMPLETED, GameState.CANCELLED}
    assert set(NOT_STARTED_STATES) == {GameState.SCHEDULED, GameState.REGISTERING}
    assert set(UNFINISHED_STATES) == {
        GameState.SCHEDULED,
        GameState.REGISTERING,
        GameState.ACTIVE,
    }
