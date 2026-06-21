"""Slice 2 — human join (no setup) and leave (pre-start free / in-match autopilot)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from itsdangerous import TimestampSigner
from sqlalchemy import select

from app.config import settings
from app.engine.human_player import get_or_create_human_agent
from app.models import GameState, Match, Player
from app.models.agent import Agent, AgentKind
from tests.factories import make_agent, make_user

GAME = "hoard-hurt-help"


def _cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(json.dumps({"user_id": user_id}).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _make_match(db, match_id: str, *, state: GameState, max_players: int = 20) -> Match:
    match = Match(
        id=match_id,
        name=f"Match {match_id}",
        game=GAME,
        state=state,
        scheduled_start=datetime.now(timezone.utc),
        per_turn_deadline_seconds=60,
        max_players=max_players,
    )
    db.add(match)
    await db.flush()
    return match


async def test_join_creates_active_human_seat(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)  # handle "agent1"
        await _make_match(db, "M_0001", state=GameState.REGISTERING)
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/join",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/games/{GAME}/matches/M_0001"

    async with reset_db() as db:
        players = (await db.execute(select(Player))).scalars().all()
        assert len(players) == 1
        p = players[0]
        assert p.user_id == user.id
        assert p.seat_name == "agent1"
        assert p.seat_reserved_until is None  # active immediately, never held
        assert p.left_at is None
        agent = (
            await db.execute(select(Agent).where(Agent.id == p.agent_id))
        ).scalar_one()
        assert agent.kind == AgentKind.HUMAN
        assert agent.provider is None


async def test_join_uses_custom_display_name(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _make_match(db, "M_0001", state=GameState.REGISTERING)
        await db.commit()

    await client.post(
        f"/games/{GAME}/matches/M_0001/play/join",
        data={"display_name": "Maverick"},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    async with reset_db() as db:
        p = (await db.execute(select(Player))).scalar_one()
        assert p.seat_name == "Maverick"


async def test_join_is_idempotent_returns_to_viewer(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _make_match(db, "M_0001", state=GameState.REGISTERING)
        await db.commit()

    for _ in range(2):
        r = await client.post(
            f"/games/{GAME}/matches/M_0001/play/join",
            cookies=_cookies(user.id),
            follow_redirects=False,
        )
        assert r.status_code == 303

    async with reset_db() as db:
        players = (await db.execute(select(Player))).scalars().all()
        assert len(players) == 1  # no duplicate seat


async def test_join_refused_when_full(reset_db, client) -> None:
    async with reset_db() as db:
        owner = await make_user(db, 1)
        await _make_match(db, "M_0001", state=GameState.REGISTERING, max_players=1)
        # seat one other player to fill it
        other = await make_user(db, 2)
        filler_agent = Agent(user_id=other.id, name="filler", kind=AgentKind.HUMAN, game=GAME)
        db.add(filler_agent)
        await db.flush()
        db.add(Player(match_id="M_0001", user_id=other.id, agent_id=filler_agent.id, seat_name="bob"))
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/join",
        cookies=_cookies(owner.id),
        follow_redirects=False,
    )
    assert r.status_code == 409


async def test_join_refused_when_not_open(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _make_match(db, "M_0001", state=GameState.ACTIVE)
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/join",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 409


async def test_join_requires_sign_in(reset_db, client) -> None:
    async with reset_db() as db:
        await _make_match(db, "M_0001", state=GameState.REGISTERING)
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/join", follow_redirects=False
    )
    assert r.status_code == 401


async def test_pre_start_leave_frees_seat(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _make_match(db, "M_0001", state=GameState.REGISTERING)
        await db.commit()
    await client.post(
        f"/games/{GAME}/matches/M_0001/play/join",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/leave",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        p = (await db.execute(select(Player))).scalar_one()
        assert p.left_at is not None  # seat freed
        assert p.autopilot_at is None


async def test_in_match_leave_sets_autopilot(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _make_match(db, "M_0001", state=GameState.REGISTERING)
        await db.commit()
    await client.post(
        f"/games/{GAME}/matches/M_0001/play/join",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    # match starts
    async with reset_db() as db:
        match = (await db.execute(select(Match))).scalar_one()
        match.state = GameState.ACTIVE
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/leave",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        p = (await db.execute(select(Player))).scalar_one()
        assert p.left_at is None  # still seated / ranked
        assert p.autopilot_at is not None  # auto-Hoards to the end


# --- consolidated join screen: "Play as yourself" is the first choice ---------


async def test_join_screen_leads_with_human_option(reset_db, client) -> None:
    """A signed-in user with no AI agent lands on the join form (not a redirect),
    with "Play as yourself" pre-selected as the first choice."""
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _make_match(db, "M_0001", state=GameState.REGISTERING)
        await db.commit()

    r = await client.get(
        f"/games/{GAME}/matches/M_0001/join",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "Play as yourself" in r.text
    # The human box is present and, with no history, is the default (checked) choice.
    assert 'name="play_as"' in r.text
    assert "data-play-as-human checked" in r.text


async def test_join_defaults_to_agent_when_last_entry_was_agent(reset_db, client) -> None:
    """Remember last choice: a user whose previous match seat was an AI agent gets
    the 'Also send an AI agent' box pre-checked (and the human box clear)."""
    async with reset_db() as db:
        user = await make_user(db, 1)
        agent, version = await make_agent(db, user, name="Atlas")
        await _make_match(db, "M_PRIOR", state=GameState.COMPLETED)
        db.add(Player(match_id="M_PRIOR", user_id=user.id, agent_id=agent.id,
                      agent_version_id=version.id, seat_name="Atlas"))
        await _make_match(db, "M_NEW", state=GameState.REGISTERING)
        await db.commit()

    r = await client.get(
        f"/games/{GAME}/matches/M_NEW/join", cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "data-play-as-agent checked" in r.text
    assert "data-play-as-human checked" not in r.text


async def test_join_defaults_to_human_when_last_entry_was_human(reset_db, client) -> None:
    """Remember last choice: a user who owns an agent but last played by hand gets
    the human box pre-checked, not the agent box."""
    async with reset_db() as db:
        user = await make_user(db, 1)
        await make_agent(db, user, name="Atlas")  # owns an agent, but last played human
        hagent, hversion = await get_or_create_human_agent(db, user, GAME)
        await _make_match(db, "M_PRIOR", state=GameState.COMPLETED)
        db.add(Player(match_id="M_PRIOR", user_id=user.id, agent_id=hagent.id,
                      agent_version_id=hversion.id, seat_name="agent1"))
        await _make_match(db, "M_NEW", state=GameState.REGISTERING)
        await db.commit()

    r = await client.get(
        f"/games/{GAME}/matches/M_NEW/join", cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "data-play-as-human checked" in r.text
    assert "data-play-as-agent checked" not in r.text


async def test_join_screen_human_submit_seats_player(reset_db, client) -> None:
    """Posting the join form with play_as=human seats a kind=human player."""
    async with reset_db() as db:
        user = await make_user(db, 1)  # handle "agent1"
        await _make_match(db, "M_0001", state=GameState.REGISTERING)
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/join",
        data={"play_as": "human"},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/games/{GAME}/matches/M_0001"

    async with reset_db() as db:
        p = (await db.execute(select(Player))).scalar_one()
        assert p.user_id == user.id
        assert p.seat_name == "agent1"  # the user's handle
        assert p.seat_reserved_until is None  # active immediately, never held
        agent = (
            await db.execute(select(Agent).where(Agent.id == p.agent_id))
        ).scalar_one()
        assert agent.kind == AgentKind.HUMAN
