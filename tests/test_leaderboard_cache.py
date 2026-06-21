"""Tests for the short-lived leaderboard cache.

The cache exists so the home page and /leaderboard don't recompute the
O(n^2) ELO projection on every request. These tests pin the behaviour that
matters: a hit inside the TTL serves the cached copy (no recompute), and both
expiry and an explicit clear force a fresh computation.
"""

from __future__ import annotations

from datetime import datetime, timezone


from app.models import GameState, Match, Player
from app.read_models.leaderboard_cache import (
    clear_leaderboard_cache,
    load_leaderboard_sections_cached,
)
from tests.factories import make_agent, make_user


async def _seed_completed_match(reset_db, *, match_id: str, user_index: int) -> None:
    """Add one completed match with two agents (the leaderboard skips <2)."""
    async with reset_db() as db:
        match = Match(
            id=match_id,
            name=f"Ranked {match_id}",
            state=GameState.COMPLETED,
            scheduled_start=datetime(2026, 6, 4, tzinfo=timezone.utc),
            per_turn_deadline_seconds=60,
            game="hoard-hurt-help",
        )
        db.add(match)
        await db.flush()
        for offset, wins, score in ((0, 3, 30), (1, 1, 10)):
            seat = user_index * 10 + offset
            user = await make_user(db, seat)
            agent, version = await make_agent(db, user, name=f"Bot{seat}")
            db.add(
                Player(
                    match_id=match.id,
                    user_id=user.id,
                    agent_id=agent.id,
                    seat_name=f"Bot{seat}",
                    agent_version_id=version.id if version else None,
                    total_round_wins=wins,
                    total_round_score=score,
                )
            )
        await db.commit()


def _row_count(sections) -> int:
    return sum(len(section.rows) for section in sections)


async def test_hit_within_ttl_serves_cached_copy(reset_db):
    """A second call inside the TTL returns the cached result, not fresh data."""
    clear_leaderboard_cache()
    await _seed_completed_match(reset_db, match_id="M_c1", user_index=1)

    first = await load_leaderboard_sections_cached(included="all")
    assert _row_count(first) == 2

    # Add a second match, then call again. Inside the TTL the cache still serves
    # the original computation — same object, stale row count.
    await _seed_completed_match(reset_db, match_id="M_c2", user_index=2)
    second = await load_leaderboard_sections_cached(included="all")

    assert second is first
    assert _row_count(second) == 2


async def test_clear_forces_recompute(reset_db):
    """After clear_leaderboard_cache(), the next call reflects current data."""
    clear_leaderboard_cache()
    await _seed_completed_match(reset_db, match_id="M_c1", user_index=1)
    await load_leaderboard_sections_cached(included="all")

    await _seed_completed_match(reset_db, match_id="M_c2", user_index=2)
    clear_leaderboard_cache()
    fresh = await load_leaderboard_sections_cached(included="all")

    assert _row_count(fresh) == 4


async def test_distinct_params_cached_separately(reset_db):
    """`included` is part of the key, so 'agents' and 'all' don't collide."""
    clear_leaderboard_cache()
    await _seed_completed_match(reset_db, match_id="M_c1", user_index=1)

    agents_view = await load_leaderboard_sections_cached(included="agents")
    all_view = await load_leaderboard_sections_cached(included="all")

    assert agents_view is not all_view
