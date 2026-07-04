"""Route tests for the spectator analysis pages (season home + round drill-in)."""

from datetime import datetime, timezone


from app.engine.tokens import generate_turn_token
from app.models import Match, GameState, Player, Turn, TurnSubmission, User
from tests.factories import make_bot


async def _seed_game_with_history(reset_db) -> None:
    """A 3-bot game: round 1 won by AI_1, round 2 (live) led by AI_0."""
    async with reset_db() as db:
        u = User(google_sub="u", email="u@t.com")
        db.add(u)
        await db.flush()
        g = Match(
            id="G_001",
            name="Test",
            state=GameState.ACTIVE,
            scheduled_start=datetime.now(timezone.utc),
            total_rounds=2,
            current_round=2,
            current_turn=1,
        )
        db.add(g)
        await db.flush()
        pids = {}
        for i in range(3):
            agent, _ = await make_bot(db, u, name=f"AI_{i}")
            p = Player(match_id="G_001", user_id=u.id, agent_id=agent.id, seat_name=f"AI_{i}")
            if i == 1:
                p.total_round_wins = 1.0
            db.add(p)
            await db.flush()
            pids[f"AI_{i}"] = p.id
        now = datetime.now(timezone.utc)

        # (round, turn, [(actor, action, target, pts, after)])
        plan = [
            (1, 1, [("AI_0", "HELP", "AI_1", 0, 0), ("AI_1", "HOARD", None, 2, 2),
                    ("AI_2", "HURT", "AI_0", 0, 0)]),
            (1, 2, [("AI_0", "HURT", "AI_1", 0, 0), ("AI_1", "HOARD", None, 2, 4),
                    ("AI_2", "HOARD", None, 2, 2)]),
            (2, 1, [("AI_0", "HOARD", None, 2, 2), ("AI_1", "HURT", "AI_0", 0, 0),
                    ("AI_2", "HOARD", None, 2, 2)]),
        ]
        for rnd, turn, subs in plan:
            t = Turn(
                match_id="G_001", round=rnd, turn=turn, turn_token=generate_turn_token(),
                opened_at=now, deadline_at=now, resolved_at=now,
            )
            db.add(t)
            await db.flush()
            for actor, action, target, pts, after in subs:
                db.add(TurnSubmission(
                    turn_id=t.id, player_id=pids[actor], action=action,
                    target_player_id=pids[target] if target else None,
                    message="", points_delta=pts, round_score_after=after,
                    was_defaulted=False, submitted_at=now,
                ))
        await db.commit()


async def test_season_page_renders(client, reset_db):
    await _seed_game_with_history(reset_db)
    r = await client.get("/games/hoard-hurt-help/matches/G_001/analysis")
    assert r.status_code == 200, r.text
    assert "Round-win standings" in r.text
    assert "Round results" in r.text
    assert "AI_1" in r.text          # round 1 winner shows in results
    assert "LIVE" in r.text          # game is active → live peek


async def test_round_drill_in_renders(client, reset_db):
    await _seed_game_with_history(reset_db)
    r = await client.get("/games/hoard-hurt-help/matches/G_001/analysis/rounds/1")
    assert r.status_code == 200, r.text
    assert "Round 1" in r.text
    assert "Leaderboard" in r.text
    assert "Betrayal" in r.text       # AI_0 helped AI_1 then hurt it


async def test_unknown_round_404(client, reset_db):
    await _seed_game_with_history(reset_db)
    r = await client.get("/games/hoard-hurt-help/matches/G_001/analysis/rounds/9")
    assert r.status_code == 404


async def test_unknown_game_404(client, reset_db):
    r = await client.get("/games/G_999/analysis")
    assert r.status_code == 404
