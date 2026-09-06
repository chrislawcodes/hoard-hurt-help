"""Phase 7: agent public messages are censored (masked), not blocked."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import TurnSubmission
from tests.test_two_phase_segregation import _seed_two_phase_game


# Autouse override of tests/conftest.py's reset_db: composes reset_pull_rate_limit
# so this file's polling tests aren't throttled by an earlier test.
@pytest.fixture(autouse=True)
async def reset_db(
    reset_db: async_sessionmaker, reset_pull_rate_limit: None
) -> async_sessionmaker:
    return reset_db


async def test_submit_masks_bad_words_in_public_text(reset_db, client):
    game, players, _resolved, open_turn = await _seed_two_phase_game(reset_db)

    resp = await client.post(
        f"/api/games/{game.id}/submit",
        params={"agent_turn_token": f"{open_turn.turn_token}:{players[0].agent_id}:{game.id}"},
        json={
            "turn_token": "open-token",
            "action": "HOARD",
            "target_id": None,
            "message": "take that you shit",
            "thinking": "i will shit on them",
        },
        headers={"X-Connection-Key": players[0]._test_key},
    )
    assert resp.status_code == 202

    async with reset_db() as db:
        sub = (
            await db.execute(
                select(TurnSubmission).where(
                    TurnSubmission.turn_id == open_turn.id,
                    TurnSubmission.player_id == players[0].id,
                )
            )
        ).scalar_one()

    # The message still posts (turn not blocked), but the bad word is censored.
    assert sub.message == "take that you ****"
    assert "shit" not in sub.thinking
    assert "****" in sub.thinking
