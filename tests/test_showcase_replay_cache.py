"""Tests for the cached cross-game showcase replay.

The cache exists so the home page doesn't rebuild a finished game's full replay
(its entire timeline) on every request. These tests pin: a hit inside the TTL
serves the cached copy (no rebuild, even after a newer game finishes), clear and
ttl=0 force a rebuild, and an empty DB falls back to the bundled sample replay.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from starlette.requests import Request
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Base, GameState, Match, Player
from app.routes.showcase_replay import (
    clear_showcase_replay_cache,
    load_showcase_replay_cached,
)
from tests.factories import make_agent, make_user

_BASE = datetime(2026, 6, 4, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)

    yield test_factory
    await test_engine.dispose()


def _make_request() -> Request:
    """Minimal anonymous request (empty session) for the replay builder."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "session": {},
        }
    )


async def _seed_showcase_match(reset_db, *, match_id: str, when: datetime, user_base: int) -> None:
    """A completed, showcase-eligible match: 3 real-agent players, public game."""
    async with reset_db() as db:
        match = Match(
            id=match_id,
            name=f"Showcase {match_id}",
            state=GameState.COMPLETED,
            scheduled_start=when,
            per_turn_deadline_seconds=60,
            game="hoard-hurt-help",
        )
        db.add(match)
        await db.flush()
        for seat in range(3):
            user = await make_user(db, user_base + seat)
            agent, version = await make_agent(db, user, name=f"Bot{user_base + seat}")
            db.add(
                Player(
                    match_id=match.id,
                    user_id=user.id,
                    agent_id=agent.id,
                    seat_name=f"Bot{user_base + seat}",
                    agent_version_id=version.id if version else None,
                    total_round_wins=seat,
                    total_round_score=seat * 10,
                )
            )
        await db.commit()


async def test_hit_within_ttl_serves_cached_copy(reset_db):
    """A newer game that finishes inside the TTL does not change the cached replay."""
    clear_showcase_replay_cache()
    await _seed_showcase_match(reset_db, match_id="M_a", when=_BASE, user_base=100)

    async with reset_db() as db:
        first = await load_showcase_replay_cached(_make_request(), db)
    assert first[0] == "M_a"  # rc_game_id

    # A newer showcase game finishes, but inside the TTL the cache still serves M_a.
    await _seed_showcase_match(reset_db, match_id="M_b", when=_BASE + timedelta(days=1), user_base=200)
    async with reset_db() as db:
        second = await load_showcase_replay_cached(_make_request(), db)
    assert second is first
    assert second[0] == "M_a"


async def test_clear_forces_rebuild(reset_db):
    """After clear, the next call reflects the newest showcase game."""
    clear_showcase_replay_cache()
    await _seed_showcase_match(reset_db, match_id="M_a", when=_BASE, user_base=100)
    async with reset_db() as db:
        await load_showcase_replay_cached(_make_request(), db)

    await _seed_showcase_match(reset_db, match_id="M_b", when=_BASE + timedelta(days=1), user_base=200)
    clear_showcase_replay_cache()
    async with reset_db() as db:
        fresh = await load_showcase_replay_cached(_make_request(), db)
    assert fresh[0] == "M_b"


async def test_zero_ttl_rebuilds(reset_db):
    """ttl_seconds=0 disables caching: each call sees the newest game."""
    clear_showcase_replay_cache()
    await _seed_showcase_match(reset_db, match_id="M_a", when=_BASE, user_base=100)
    async with reset_db() as db:
        first = await load_showcase_replay_cached(_make_request(), db, ttl_seconds=0)
    assert first[0] == "M_a"

    await _seed_showcase_match(reset_db, match_id="M_b", when=_BASE + timedelta(days=1), user_base=200)
    async with reset_db() as db:
        second = await load_showcase_replay_cached(_make_request(), db, ttl_seconds=0)
    assert second[0] == "M_b"


async def test_no_showcase_falls_back_to_sample(reset_db):
    """No finished games at all → fall back to the bundled sample replay."""
    clear_showcase_replay_cache()
    async with reset_db() as db:
        rc_game_id, rc_data, rc_game_type = await load_showcase_replay_cached(_make_request(), db)
    assert rc_game_id is None
    assert rc_game_type is None
    assert rc_data  # non-empty sample JSON so the animation still plays
