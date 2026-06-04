"""Tests for app.engine.arena — Practice Arena and Auto-Match lifecycle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import make_engine
from app.engine.arena import (
    AUTO_MATCH_MAX_PLAYERS,
    PRACTICE_ARENA_MAX_PLAYERS,
    PRACTICE_ARENA_NAME,
    PRACTICE_ARENA_SIM_COUNT,
    ensure_auto_match,
    ensure_practice_arena,
    fill_and_start_auto_matches,
)
from app.models import Base
from app.models.match import GameState, Match, MatchKind
from app.models.player import Player


@pytest.fixture(autouse=True)
async def db_session(monkeypatch):
    """Fresh in-memory SQLite DB per test; SessionLocal patched to match."""
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    monkeypatch.setattr("app.db.engine", engine)

    yield factory
    await engine.dispose()


# ---------------------------------------------------------------------------
# Practice Arena tests
# ---------------------------------------------------------------------------


async def test_ensure_creates_practice_arena_when_none_exists(db_session):
    async with db_session() as db:
        await ensure_practice_arena(db)

    async with db_session() as db:
        arenas = (
            await db.execute(
                select(Match).where(Match.match_kind == MatchKind.PRACTICE_ARENA.value)
            )
        ).scalars().all()
        assert len(arenas) == 1
        arena = arenas[0]
        assert arena.name == PRACTICE_ARENA_NAME
        assert arena.state == GameState.REGISTERING
        assert arena.max_players == PRACTICE_ARENA_MAX_PLAYERS

        # Should have PRACTICE_ARENA_SIM_COUNT pre-seated Sim players.
        sim_count = await db.scalar(
            select(func.count()).select_from(Player).where(
                Player.match_id == arena.id, Player.left_at.is_(None)
            )
        )
        assert sim_count == PRACTICE_ARENA_SIM_COUNT


async def test_ensure_practice_arena_idempotent(db_session):
    async with db_session() as db:
        await ensure_practice_arena(db)
    async with db_session() as db:
        await ensure_practice_arena(db)

    async with db_session() as db:
        count = await db.scalar(
            select(func.count()).select_from(Match).where(
                Match.match_kind == MatchKind.PRACTICE_ARENA.value
            )
        )
        assert count == 1


async def test_ensure_practice_arena_recreates_after_completion(db_session):
    async with db_session() as db:
        await ensure_practice_arena(db)
        arena = (
            await db.execute(
                select(Match).where(Match.match_kind == MatchKind.PRACTICE_ARENA.value)
            )
        ).scalars().first()
        assert arena is not None

        arena.state = GameState.COMPLETED
        arena.completed_at = datetime.now(timezone.utc)
        await db.commit()

    async with db_session() as db:
        await ensure_practice_arena(db)

    async with db_session() as db:
        open_arenas = (
            await db.execute(
                select(Match).where(
                    Match.match_kind == MatchKind.PRACTICE_ARENA.value,
                    Match.state.in_([GameState.SCHEDULED, GameState.REGISTERING]),
                )
            )
        ).scalars().all()
        assert len(open_arenas) == 1


# ---------------------------------------------------------------------------
# Auto-Match tests
# ---------------------------------------------------------------------------


async def test_ensure_auto_match_creates_when_none(db_session):
    async with db_session() as db:
        await ensure_auto_match(db)

    async with db_session() as db:
        matches = (
            await db.execute(
                select(Match).where(Match.match_kind == MatchKind.AUTO_SCHEDULED.value)
            )
        ).scalars().all()
        assert len(matches) == 1
        m = matches[0]
        assert m.state == GameState.SCHEDULED
        assert m.max_players == AUTO_MATCH_MAX_PLAYERS
        # Boundary must be in the future.
        now = datetime.now(timezone.utc)
        scheduled = m.scheduled_start
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        assert scheduled > now


async def test_ensure_auto_match_idempotent(db_session):
    async with db_session() as db:
        await ensure_auto_match(db)
    async with db_session() as db:
        await ensure_auto_match(db)

    async with db_session() as db:
        count = await db.scalar(
            select(func.count()).select_from(Match).where(
                Match.match_kind == MatchKind.AUTO_SCHEDULED.value
            )
        )
        assert count == 1


async def test_fill_and_start_auto_matches_fills_sims(db_session):
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    match_id = "M_9001"

    async with db_session() as db:
        m = Match(
            id=match_id,
            name="Auto Match 00:00",
            state=GameState.SCHEDULED,
            scheduled_start=past,
            min_players=1,
            max_players=AUTO_MATCH_MAX_PLAYERS,
            match_kind=MatchKind.AUTO_SCHEDULED.value,
        )
        db.add(m)
        await db.commit()

    async with db_session() as db:
        await fill_and_start_auto_matches(db)

    async with db_session() as db:
        m = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()
        assert m.state == GameState.ACTIVE

        player_count = await db.scalar(
            select(func.count()).select_from(Player).where(
                Player.match_id == match_id, Player.left_at.is_(None)
            )
        )
        # Sims fill up to AUTO_MATCH_SIM_COUNT_MAX (one human slot left open).
        from app.engine.arena import AUTO_MATCH_SIM_COUNT_MAX
        assert player_count == min(AUTO_MATCH_MAX_PLAYERS, AUTO_MATCH_SIM_COUNT_MAX)


async def test_fill_and_start_auto_matches_zero_humans(db_session):
    """Even with 0 humans, an overdue auto-match should start filled with Sims."""
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    match_id = "M_9002"

    async with db_session() as db:
        m = Match(
            id=match_id,
            name="Auto Match 00:30",
            state=GameState.SCHEDULED,
            scheduled_start=past,
            min_players=1,
            max_players=AUTO_MATCH_MAX_PLAYERS,
            match_kind=MatchKind.AUTO_SCHEDULED.value,
        )
        db.add(m)
        await db.commit()

    async with db_session() as db:
        await fill_and_start_auto_matches(db)

    async with db_session() as db:
        m = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()
        assert m.state == GameState.ACTIVE

        player_count = await db.scalar(
            select(func.count()).select_from(Player).where(
                Player.match_id == match_id, Player.left_at.is_(None)
            )
        )
        assert player_count > 0
