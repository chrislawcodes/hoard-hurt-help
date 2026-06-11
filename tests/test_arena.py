"""Tests for app.engine.arena — Practice Arena and Auto-Match lifecycle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import make_engine
from app.engine.arena import (
    AUTO_MATCH_MAX_PLAYERS,
    AUTO_MATCH_BOT_COUNT_MAX,
    PRACTICE_ARENA_MAX_PLAYERS,
    PRACTICE_ARENA_NAME,
    PRACTICE_ARENA_BOT_COUNT,
    ensure_auto_match,
    ensure_practice_arena,
    fill_and_start_auto_matches,
)
from app.engine.bot_presets import HISTORICAL_BOT_NAME_POOL
from app.engine.sims.seating import SimSeatingError
from app.models import Base
from app.models.match import GameState, Match, MatchKind
from app.models.player import Player
from tests.factories import seat_player


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
        assert arena.created_by_user_id is None

        # Should have PRACTICE_ARENA_BOT_COUNT pre-seated Sim players.
        sim_count = await db.scalar(
            select(func.count()).select_from(Player).where(
                Player.match_id == arena.id, Player.left_at.is_(None)
            )
        )
        assert sim_count == PRACTICE_ARENA_BOT_COUNT
        seat_names = (
            (
                await db.execute(
                    select(Player.seat_name)
                    .where(Player.match_id == arena.id, Player.left_at.is_(None))
                    .order_by(Player.id)
                )
            )
            .scalars()
            .all()
        )
        assert seat_names == list(HISTORICAL_BOT_NAME_POOL[:PRACTICE_ARENA_BOT_COUNT])


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


async def test_ensure_practice_arena_recovers_from_empty_arena(db_session):
    """An existing REGISTERING arena with 0 bots is cancelled and replaced."""
    async with db_session() as db:
        await ensure_practice_arena(db)
        arena = (
            await db.execute(
                select(Match).where(Match.match_kind == MatchKind.PRACTICE_ARENA.value)
            )
        ).scalars().first()
        assert arena is not None
        stale_id = arena.id

        # Simulate what migration 0023 did: wipe the players table rows for this
        # arena so it appears to have no bots, but stays in REGISTERING state.
        await db.execute(delete(Player).where(Player.match_id == stale_id))
        await db.commit()

    async with db_session() as db:
        await ensure_practice_arena(db)

    async with db_session() as db:
        # The stale arena should be cancelled and a new one created.
        stale = (
            await db.execute(select(Match).where(Match.id == stale_id))
        ).scalar_one()
        assert stale.state == GameState.CANCELLED

        new_arena = (
            await db.execute(
                select(Match).where(
                    Match.match_kind == MatchKind.PRACTICE_ARENA.value,
                    Match.state.in_([GameState.SCHEDULED, GameState.REGISTERING]),
                )
            )
        ).scalars().first()
        assert new_arena is not None
        assert new_arena.id != stale_id

        bot_count = await db.scalar(
            select(func.count()).select_from(Player).where(
                Player.match_id == new_arena.id, Player.left_at.is_(None)
            )
        )
        assert bot_count == PRACTICE_ARENA_BOT_COUNT


async def test_ensure_practice_arena_refreshes_stale_capacity(db_session):
    """An arena whose max_players predates the current constant is replaced."""
    async with db_session() as db:
        await ensure_practice_arena(db)
        arena = (
            await db.execute(
                select(Match).where(Match.match_kind == MatchKind.PRACTICE_ARENA.value)
            )
        ).scalars().first()
        assert arena is not None
        stale_id = arena.id

        # Simulate an arena created before PRACTICE_ARENA_MAX_PLAYERS changed: same
        # bots, but an out-of-date capacity baked into the row.
        arena.max_players = PRACTICE_ARENA_MAX_PLAYERS + 1
        await db.commit()

    async with db_session() as db:
        await ensure_practice_arena(db)

    async with db_session() as db:
        stale = (
            await db.execute(select(Match).where(Match.id == stale_id))
        ).scalar_one()
        assert stale.state == GameState.CANCELLED

        new_arena = (
            await db.execute(
                select(Match).where(
                    Match.match_kind == MatchKind.PRACTICE_ARENA.value,
                    Match.state.in_([GameState.SCHEDULED, GameState.REGISTERING]),
                )
            )
        ).scalars().first()
        assert new_arena is not None
        assert new_arena.id != stale_id
        assert new_arena.max_players == PRACTICE_ARENA_MAX_PLAYERS


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
        await db.flush()
        await seat_player(db, match_id, "Human1", i=1)
        await db.commit()

    async with db_session() as db:
        await fill_and_start_auto_matches(db)

    async with db_session() as db:
        m = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()
        assert m.state == GameState.ACTIVE
        assert m.created_by_user_id is None

        player_count = await db.scalar(
            select(func.count()).select_from(Player).where(
                Player.match_id == match_id, Player.left_at.is_(None)
            )
        )
        expected_count = 1 + min(AUTO_MATCH_MAX_PLAYERS - 1, AUTO_MATCH_BOT_COUNT_MAX)
        assert player_count == expected_count
        seat_names = set(
            (
                await db.execute(
                    select(Player.seat_name).where(
                        Player.match_id == match_id, Player.left_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert seat_names == {"Human1", *HISTORICAL_BOT_NAME_POOL[: player_count - 1]}


async def test_fill_and_start_auto_matches_zero_humans_cancels(db_session):
    """An overdue auto-match should not run when no external agents joined."""
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
        assert m.state == GameState.CANCELLED
        assert m.cancelled_at is not None
        assert m.created_by_user_id is None

        player_count = await db.scalar(
            select(func.count()).select_from(Player).where(
                Player.match_id == match_id, Player.left_at.is_(None)
            )
        )
        assert player_count == 0


async def test_fill_and_start_seating_error_cancels_match_and_continues(db_session):
    """A SimSeatingError during bot seating cancels the affected match.

    The failed match must end up CANCELLED (not left in limbo), and the loop
    must continue processing subsequent due matches.
    """
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    failing_id = "M_9010"
    succeeding_id = "M_9011"

    async with db_session() as db:
        # First match: has a human, but bot seating will fail.
        m1 = Match(
            id=failing_id,
            name="Failing Match",
            state=GameState.SCHEDULED,
            scheduled_start=past,
            min_players=1,
            max_players=AUTO_MATCH_MAX_PLAYERS,
            match_kind=MatchKind.AUTO_SCHEDULED.value,
        )
        db.add(m1)
        await db.flush()
        await seat_player(db, failing_id, "Human1", i=1)

        # Second match: also has a human, and bot seating will succeed normally.
        m2 = Match(
            id=succeeding_id,
            name="Succeeding Match",
            state=GameState.SCHEDULED,
            scheduled_start=past,
            min_players=1,
            max_players=AUTO_MATCH_MAX_PLAYERS,
            match_kind=MatchKind.AUTO_SCHEDULED.value,
        )
        db.add(m2)
        await db.flush()
        await seat_player(db, succeeding_id, "Human2", i=2)
        await db.commit()

    # Patch add_bots_to_game so it raises only for the first match.
    original_add_bots = "app.engine.arena.add_bots_to_game"
    call_count = 0

    async def _add_bots_side_effect(db, match, seats):
        nonlocal call_count
        call_count += 1
        if match.id == failing_id:
            raise SimSeatingError("Test-induced seating failure")
        from app.engine.sims.seating import add_bots_to_game as _real
        return await _real(db, match, seats)

    with patch(original_add_bots, side_effect=_add_bots_side_effect):
        # Also patch start_game to avoid spinning up asyncio game tasks.
        with patch("app.engine.scheduler.start_game", new_callable=AsyncMock):
            async with db_session() as db:
                await fill_and_start_auto_matches(db)

    async with db_session() as db:
        # The failing match must be CANCELLED with a timestamp.
        m1 = (
            await db.execute(select(Match).where(Match.id == failing_id))
        ).scalar_one()
        assert m1.state == GameState.CANCELLED
        assert m1.cancelled_at is not None
        assert m1.created_by_user_id is None

        # The succeeding match must have been processed (ACTIVE or still in DB).
        # We at minimum verify the loop did not stop after the first failure.
        m2 = (
            await db.execute(select(Match).where(Match.id == succeeding_id))
        ).scalar_one()
        # The loop continued past the failure to attempt seating the second match.
        assert call_count == 2
        assert m2.created_by_user_id is None
