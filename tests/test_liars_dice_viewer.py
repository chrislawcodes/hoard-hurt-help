"""Regression tests for the Liar's Dice game viewer.

Locks in the LD-specific viewer path added in the game-agnostic viewer
refactor (PR #439): build_replay_view returns no replay data for LD, and
viewer_fragment selects liars_dice_live_region.html instead of the PD feed.

These three checks protect that contract:
  1. The full viewer page returns 200 and contains LD-specific feed markers.
  2. The live fragment endpoint returns 200 with those same markers.
  3. The PD robot-circle replay stage (rc-stage) is absent for LD matches.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import GameState, Match, MatchState, PlayerState
from tests.factories import seat_player

MATCH_ID = "M_LD_VIEW"


async def _seed(reset_db) -> None:
    now = datetime.now(timezone.utc)
    async with reset_db() as db:
        match = Match(
            id=MATCH_ID,
            name="Liar's Dice Test",
            game="liars-dice",
            state=GameState.ACTIVE,
            scheduled_start=now,
            started_at=now,
            current_round=1,
            current_turn=1,
            per_turn_deadline_seconds=30,
            total_rounds=64,
            turns_per_round=256,
        )
        db.add(match)
        await db.flush()

        players = [
            await seat_player(db, MATCH_ID, "A", i=0),
            await seat_player(db, MATCH_ID, "B", i=1),
            await seat_player(db, MATCH_ID, "C", i=2),
        ]

        db.add(
            MatchState(
                match_id=MATCH_ID,
                state_json={
                    "config": {"wild_ones": True, "dice_per_player": 5},
                    "hand": 1,
                    "active_actor": "A",
                    "standing_bid": None,
                    "challenge_pending": False,
                },
            )
        )
        for player in players:
            db.add(
                PlayerState(
                    match_id=MATCH_ID,
                    player_id=player.id,
                    state_json={"dice": [1, 2, 3, 4, 5], "dice_count": 5},
                )
            )
        await db.commit()


@pytest.mark.asyncio
async def test_ld_viewer_page_renders_ld_feed(client, reset_db):
    """Full viewer page serves 200 and includes the LD-specific feed section."""
    await _seed(reset_db)
    r = await client.get(f"/games/liars-dice/matches/{MATCH_ID}")
    assert r.status_code == 200
    # Section class from liars_dice_live_region.html (not the PD feed).
    assert "feed-col feed-col-full" in r.text
    # Game-head meta rendered from public_state.wild_ones.
    assert "Wild ones" in r.text


@pytest.mark.asyncio
async def test_ld_live_fragment_renders_ld_feed(client, reset_db):
    """Live fragment endpoint serves 200 and includes the LD-specific feed."""
    await _seed(reset_db)
    r = await client.get(f"/games/liars-dice/matches/{MATCH_ID}/live")
    assert r.status_code == 200
    assert "feed-col feed-col-full" in r.text
    assert "Wild ones" in r.text


@pytest.mark.asyncio
async def test_ld_viewer_has_no_robot_circle_stage(client, reset_db):
    """The PD robot-circle stage must be absent for Liar's Dice matches."""
    await _seed(reset_db)
    r = await client.get(f"/games/liars-dice/matches/{MATCH_ID}")
    assert r.status_code == 200
    assert "rc-stage" not in r.text
