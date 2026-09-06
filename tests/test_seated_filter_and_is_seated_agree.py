"""Agreement test for the two forms of "is this player still seated?".

CLAUDE.md's One Home Per Rule requires this: when a rule genuinely exists in
two forms — here, `app.engine.seated.seated_filter()` for a SQLAlchemy query
and `is_seated()` for a Python object already in hand — a test must exercise
both and assert they agree, since nothing else stops them drifting apart.

Seats several players in a real test DB, some left, some not, then checks
that the exact set of players a `seated_filter()` query returns is the same
set for which `is_seated(player)` is True — verified over every seated row
in the match, including the left one, not just a spot check.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.engine.seated import is_seated, seated_filter
from app.models import GameState, Match, Player
from tests.factories import make_agent, make_user


async def test_seated_filter_and_is_seated_agree_on_every_row(db) -> None:
    match_id = "G_SEATED"
    user = await make_user(db, 0)
    stayed_agent, _ = await make_agent(db, user, name="stayed")
    also_stayed_agent, _ = await make_agent(db, user, name="also_stayed")
    left_agent, _ = await make_agent(db, user, name="left")
    db.add(
        Match(
            id=match_id,
            name="seated agreement",
            state=GameState.ACTIVE,
            scheduled_start=datetime.now(timezone.utc),
            per_turn_deadline_seconds=60,
        )
    )
    db.add(
        Player(
            match_id=match_id,
            user_id=user.id,
            agent_id=stayed_agent.id,
            seat_name="stayed",
        )
    )
    db.add(
        Player(
            match_id=match_id,
            user_id=user.id,
            agent_id=also_stayed_agent.id,
            seat_name="also_stayed",
        )
    )
    db.add(
        Player(
            match_id=match_id,
            user_id=user.id,
            agent_id=left_agent.id,
            seat_name="left",
            left_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()

    # Every seat in the match, whichever way it went — the ground truth to
    # check both forms of the rule against.
    all_players = (
        (await db.execute(select(Player).where(Player.match_id == match_id)))
        .scalars()
        .all()
    )
    assert len(all_players) == 3  # sanity: the seed above actually landed

    seated_by_query = {
        p.id
        for p in (
            await db.execute(
                select(Player).where(Player.match_id == match_id, seated_filter())
            )
        )
        .scalars()
        .all()
    }

    for player in all_players:
        assert (player.id in seated_by_query) == is_seated(player), (
            f"seated_filter() and is_seated() disagree on seat {player.seat_name!r} "
            f"(left_at={player.left_at!r})"
        )

    # Not a vacuous check: both the seated and the left case are represented.
    assert seated_by_query == {p.id for p in all_players if p.seat_name != "left"}
