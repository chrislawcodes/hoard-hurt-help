"""The Agent Ludum front page shows only real data — never fabricated rows.

The design handoff shipped fictional ELO ratings, @owner handles, and a
"find a rival in ~3s" promise. The page must show none of that: real standings
from a real game, or an honest empty state.
"""

from datetime import datetime, timedelta, timezone


from app.models import Match, GameState
from tests.factories import seat_player


def _assert_no_fabricated_marketing_claims(text: str) -> None:
    """None of the prototype's invented content may reach the page."""
    assert "ELO" not in text
    assert "matchmaking" not in text.lower()
    assert "rival in" not in text.lower()


async def test_empty_state_has_no_fabricated_rows(client, reset_db):
    """With zero games the page still renders, with an honest empty state."""
    r = await client.get("/")
    assert r.status_code == 200
    _assert_no_fabricated_marketing_claims(r.text)
    # Honest empty copy for the standings band, not invented leaderboard rows.
    assert "No ranked competitors yet" in r.text


async def test_standings_band_shows_real_agents(client, reset_db):
    """A finished showcase game's real agents appear in the standings band."""
    async with reset_db() as db:
        g = Match(
            id="G_done",
            name="Final Showdown",
            state=GameState.COMPLETED,
            scheduled_start=datetime.now(timezone.utc) - timedelta(hours=2),
            per_turn_deadline_seconds=60,
        )
        db.add(g)
        # A showcase game needs a full table (>= 3 active players).
        for i, (agent, score) in enumerate(
            [("Claudius", 22), ("Sonnet_Sue", 17), ("GPT_Greg", 9)]
        ):
            p = await seat_player(db, "G_done", agent, i=i)
            p.current_round_score = score
        await db.commit()

    r = await client.get("/")
    assert r.status_code == 200
    assert "Claudius" in r.text  # a real agent_id, straight from the DB
    _assert_no_fabricated_marketing_claims(r.text)
