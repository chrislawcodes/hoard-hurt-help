"""Leaderboard placement is per-game; PD's Elo numbers are unchanged.

The rating engine (Elo pairings, K-factor, first-place bonus) is shared across
games; how a finished match ranks its players is per-game. PD's placement is
round-wins then total score, so its ratings must not move when placement becomes
a module hook (parity). A second test proves a game that overrides the placement
key reorders its own section without touching PD.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.games as registry
from app.db import make_engine
from app.games.base import BaseGameModule
from app.models import Base, Match, GameState
from app.read_models.leaderboard import load_leaderboard_sections
from tests.factories import seat_player


def _at() -> datetime:
    # On/after the leaderboard cutoff.
    return datetime(2026, 6, 10, tzinfo=timezone.utc)


async def _seed_completed_match(
    db: Any, match_id: str, game_type: str, stats: list[tuple[str, float, int]]
) -> None:
    match = Match(
        id=match_id,
        name=match_id,
        game=game_type,
        state=GameState.COMPLETED,
        scheduled_start=_at(),
        completed_at=_at(),
        total_rounds=7,
    )
    db.add(match)
    await db.flush()
    for i, (seat, wins, score) in enumerate(stats):
        p = await seat_player(db, match.id, seat, i=i)
        p.total_round_wins = wins
        p.total_round_score = score
    await db.commit()


@pytest.mark.asyncio
async def test_pd_leaderboard_ratings_unchanged() -> None:
    """PD parity: exact Elo ratings for a known 3-agent match (baseline-captured)."""
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        await _seed_completed_match(
            db, "M_LB", "hoard-hurt-help",
            [("A", 3.0, 120), ("B", 2.0, 90), ("C", 1.0, 60)],
        )
        standard = (await load_leaderboard_sections(db, rating_mode="standard", included="agents"))[0]
        assert [(r.rank, r.rating) for r in standard.rows] == [(1, 1512.0), (2, 1500.0), (3, 1488.0)]
        bonus = (await load_leaderboard_sections(db, rating_mode="bonus", included="agents"))[0]
        assert [(r.rank, round(r.rating, 4)) for r in bonus.rows] == [
            (1, 1514.4), (2, 1498.8), (3, 1486.8),
        ]

    await engine.dispose()


class _ScoreOnlyGame(BaseGameModule):
    """A game whose placement is total_score only (ignores round_wins)."""

    game_type = "score-only-test"

    def match_placement_key(self, *, round_wins: float, total_score: int) -> tuple[float, ...]:
        return (float(total_score),)


@pytest.mark.asyncio
async def test_per_game_placement_key_reorders_its_section() -> None:
    """A module's match_placement_key drives its own section's finish order."""
    registry.register(_ScoreOnlyGame())

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        # X wins more rounds but Y has the higher total score. PD's default key
        # would rank X first; the score-only game must rank Y first.
        await _seed_completed_match(
            db, "M_SO", "score-only-test", [("X", 5.0, 10), ("Y", 1.0, 99)]
        )
        section = (await load_leaderboard_sections(db, included="agents"))[0]
        assert section.game_type == "score-only-test"
        assert [r.rank for r in section.rows] == [1, 2]
        # Y (higher score) outranks X (more wins) under the score-only key.
        assert section.rows[0].rating > section.rows[1].rating
        top_names = section.rows[0].display_name
        assert "Y" in top_names

    await engine.dispose()
