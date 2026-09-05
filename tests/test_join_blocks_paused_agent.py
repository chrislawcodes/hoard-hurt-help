"""A paused agent must not be able to take a seat.

THE BUG THIS PINS. The join picker selected `kind=AI + not archived` while every
turn-serving query also required `status=ACTIVE`. A paused agent therefore
appeared in the picker, was accepted by the seating route, and was then skipped
by every serving query for the rest of the match -- the overdue sweeper defaulted
its moves (HOARD) turn after turn while the owner saw no error anywhere.

Both halves are covered here on purpose: the picker must SAY it (and say what
fixes it), and the route must ENFORCE it, because a crafted POST never touches
the template. Both now read `seat_block`, so they cannot disagree.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.models import GameState, Player
from app.models.agent import AgentStatus
from tests.conftest import signed_in_cookies as _cookies
from tests.factories import make_agent, make_connection, make_user, seed_match

JOIN_URL = "/games/hoard-hurt-help/matches/G_001/join"


async def _user_with_agent(reset_db, *, status: AgentStatus):
    async with reset_db() as db:
        user = await make_user(db, 0)
        connection, _ = await make_connection(db, user)
        now = datetime.now(timezone.utc)
        connection.mcp_connected_at = now
        connection.first_connected_at = now
        connection.last_seen_at = now
        connection.last_polled_at = now
        agent, _v = await make_agent(db, user, connection=connection, name="Atlas")
        agent.status = status
        await db.commit()
        return user.id, agent.id


async def test_picker_shows_a_paused_agent_as_blocked_with_a_way_to_fix_it(
    client, reset_db
) -> None:
    """Shown, not hidden -- a vanished agent reads as deleted and sends people
    hunting. The row must name the reason and link to the control that fixes it."""
    await seed_match(reset_db, "G_001", state=GameState.REGISTERING, name="Test Match")
    user_id, agent_id = await _user_with_agent(reset_db, status=AgentStatus.PAUSED)

    page = await client.get(JOIN_URL, cookies=_cookies(user_id))
    assert page.status_code == 200
    assert "Atlas" in page.text, "the agent should still be listed, not hidden"
    assert "Paused" in page.text
    assert f"/me/agents/{agent_id}" in page.text, "no link to where Resume lives"
    assert "resume it" in page.text
    # A blocked row renders no checkbox mirrors, so it cannot post an agent_id.
    assert f'value="{agent_id}" disabled data-agent-id-mirror' not in page.text


async def test_seating_a_paused_agent_is_refused(client, reset_db) -> None:
    """The route enforces it independently of the picker: a crafted POST never
    renders a template, so a UI-only fix would leave the hole open."""
    await seed_match(reset_db, "G_001", state=GameState.REGISTERING, name="Test Match")
    user_id, agent_id = await _user_with_agent(reset_db, status=AgentStatus.PAUSED)

    r = await client.post(
        JOIN_URL,
        data={"agent_id": agent_id, "chosen_provider": "claude"},
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 403, r.text
    assert "paused" in r.text.lower()
    async with reset_db() as db:
        seats = (await db.execute(select(Player))).scalars().all()
        assert seats == [], "a paused agent must not end up holding a seat"


async def test_an_active_agent_still_joins(client, reset_db) -> None:
    """The guard must not block the normal path."""
    await seed_match(reset_db, "G_001", state=GameState.REGISTERING, name="Test Match")
    user_id, agent_id = await _user_with_agent(reset_db, status=AgentStatus.ACTIVE)

    r = await client.post(
        JOIN_URL,
        data={"agent_id": agent_id, "chosen_provider": "claude"},
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    async with reset_db() as db:
        seats = (await db.execute(select(Player))).scalars().all()
        assert len(seats) == 1


async def test_resume_sends_the_user_back_to_the_match_they_came_from(
    client, reset_db
) -> None:
    """Without this the fix-link is a dead end: you resume, land on the agent page,
    and have to find your way back to a match that may be filling up."""
    await seed_match(reset_db, "G_001", state=GameState.REGISTERING, name="Test Match")
    user_id, agent_id = await _user_with_agent(reset_db, status=AgentStatus.PAUSED)

    r = await client.post(
        f"/me/agents/{agent_id}/resume",
        data={"next": JOIN_URL},
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == JOIN_URL


async def test_resume_ignores_an_external_next(client, reset_db) -> None:
    """`safe_internal_next` guards the open redirect; pin it on this caller too."""
    await seed_match(reset_db, "G_001", state=GameState.REGISTERING, name="Test Match")
    user_id, agent_id = await _user_with_agent(reset_db, status=AgentStatus.PAUSED)

    r = await client.post(
        f"/me/agents/{agent_id}/resume",
        data={"next": "https://evil.example/steal"},
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/me/agents/{agent_id}"
