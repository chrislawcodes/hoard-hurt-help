"""C8 dedup: the field-only cancel transition.

Pins that `mark_cancelled(match, now)` writes exactly the two fields and takes
`now` as a parameter (so each call site keeps its own fresh-or-captured
timestamp). It must NOT commit or stop the registry — those stay with the
callers (`cancel_match` keeps `registry.stop`; the inline sites keep their own
commit). A merge that absorbed `registry.stop` into the helper would need a DB /
registry and break this pure unit test.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.engine.match_cancellation import mark_cancelled
from app.models.match import GameState, Match


def test_mark_cancelled_sets_both_fields_to_passed_now() -> None:
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    match = Match(id="G_X", name="x", state=GameState.ACTIVE)
    mark_cancelled(match, now)
    assert match.state == GameState.CANCELLED
    assert match.cancelled_at == now


def test_mark_cancelled_uses_the_now_it_is_given() -> None:
    # Two different timestamps → the field reflects exactly what the caller passed
    # (captured-batch now vs a fresh now per site stays the caller's choice).
    early = datetime(2026, 1, 1, tzinfo=timezone.utc)
    match = Match(id="G_Y", name="y", state=GameState.ACTIVE)
    mark_cancelled(match, early)
    assert match.cancelled_at == early
