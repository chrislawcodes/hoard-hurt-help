"""C4 dedup: the shared non-left player count.

Pins the two filter modes that the watchdog (left-only, INCLUDES held seats) and
the start-floor / arena-confirmed counts (left + reserved-excluded) depend on, so
collapsing them into one `active_player_count(..., exclude_reserved=...)` helper
cannot silently flip a filter. A reserved-aware watchdog would cancel a
held-seat-only ACTIVE game — this test fails if that regression is introduced.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.engine.player_counts import active_player_count
from app.models import GameState, Match, Player
from tests.factories import make_agent, make_user


async def _seed(db) -> str:
    match_id = "G_C4"
    user = await make_user(db, 0)
    held_agent, _ = await make_agent(db, user, name="held")
    confirmed_agent, _ = await make_agent(db, user, name="confirmed")
    left_agent, _ = await make_agent(db, user, name="left")
    db.add(
        Match(
            id=match_id,
            name="C4 counts",
            state=GameState.ACTIVE,
            scheduled_start=datetime.now(timezone.utc),
            per_turn_deadline_seconds=60,
        )
    )
    # Held seat (reserved, not yet confirmed), still present.
    db.add(
        Player(
            match_id=match_id,
            user_id=user.id,
            agent_id=held_agent.id,
            seat_name="held",
            seat_reserved_until=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
    )
    # Confirmed seat (present, not reserved).
    db.add(
        Player(
            match_id=match_id,
            user_id=user.id,
            agent_id=confirmed_agent.id,
            seat_name="confirmed",
        )
    )
    # Left seat — counts toward neither filter.
    db.add(
        Player(
            match_id=match_id,
            user_id=user.id,
            agent_id=left_agent.id,
            seat_name="left",
            left_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()
    return match_id


async def test_exclude_reserved_true_is_confirmed_only(db) -> None:
    """Start-floor / arena-confirmed: held seat does NOT count."""
    match_id = await _seed(db)
    assert await active_player_count(db, match_id, exclude_reserved=True) == 1


async def test_exclude_reserved_false_includes_held(db) -> None:
    """Watchdog / arena-seated: held seat DOES count (so a held-only ACTIVE game
    is not seen as empty and cancelled). Left seat still excluded."""
    match_id = await _seed(db)
    assert await active_player_count(db, match_id, exclude_reserved=False) == 2
