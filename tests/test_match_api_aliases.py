"""API alias tests for canonical match IDs and legacy game IDs."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import GameState, Match
from tests.factories import seat_player


async def _seed_match_with_player(
    reset_db: async_sessionmaker,
) -> tuple[str, str]:
    async with reset_db() as db:
        match = Match(
            id="M_001",
            name="Alias Test",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            per_turn_deadline_seconds=60,
        )
        db.add(match)
        await db.flush()
        player = await seat_player(db, "M_001", "AI_0")
        key = getattr(player, "_test_key")
        await db.commit()
        return "M_001", key


@pytest.mark.asyncio
async def test_agent_state_accepts_canonical_and_legacy_prefixes(client, reset_db):
    match_id, agent_key = await _seed_match_with_player(reset_db)
    headers = {"X-Connection-Key": agent_key}

    canonical = await client.get(f"/api/matches/{match_id}/state", headers=headers)
    legacy = await client.get(f"/api/games/{match_id}/state", headers=headers)

    assert canonical.status_code == 200
    assert legacy.status_code == 200
    assert canonical.json() == legacy.json()
    assert canonical.json()["match_id"] == match_id
    assert canonical.json()["game_id"] == match_id


@pytest.mark.asyncio
async def test_spectator_state_accepts_canonical_and_legacy_prefixes(client, reset_db):
    match_id, _ = await _seed_match_with_player(reset_db)

    canonical = await client.get(f"/api/spectator/matches/{match_id}/state")
    legacy = await client.get(f"/api/spectator/games/{match_id}/state")

    assert canonical.status_code == 200
    assert legacy.status_code == 200
    assert canonical.json() == legacy.json()
    assert canonical.json()["match_id"] == match_id
    assert canonical.json()["game_id"] == match_id
