"""Slice 3 — the play panel renders for the seated human only, with the right state."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.engine.human_player import get_or_create_human_agent
from app.engine.tokens import generate_turn_token
from app.main import app
from app.models import Base, GameState, Match, Player, User
from app.models.turn import Turn
from tests.factories import make_user, seat_player

GAME = "hoard-hurt-help"
VIEWER = f"/games/{GAME}/matches/M_0001"
LIVE = f"{VIEWER}/live"


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    monkeypatch.setattr("app.db.engine", engine)
    yield factory
    await engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(json.dumps({"user_id": user_id}).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _match(db, *, state: GameState) -> Match:
    match = Match(
        id="M_0001",
        name="Match M_0001",
        game=GAME,
        state=state,
        scheduled_start=datetime.now(timezone.utc),
        per_turn_deadline_seconds=60,
        max_players=20,
    )
    db.add(match)
    await db.flush()
    return match


async def _seat_human(db, user: User, seat_name: str) -> Player:
    agent, version = await get_or_create_human_agent(db, user, GAME)
    player = Player(
        match_id="M_0001",
        user_id=user.id,
        agent_id=agent.id,
        agent_version_id=version.id,
        seat_name=seat_name,
    )
    db.add(player)
    await db.flush()
    return player


async def _open_turn(db, phase: str) -> Turn:
    now = datetime.now(timezone.utc)
    turn = Turn(
        match_id="M_0001",
        round=1,
        turn=1,
        turn_token=generate_turn_token(),
        opened_at=now,
        deadline_at=now + timedelta(seconds=60),
        phase=phase,
    )
    db.add(turn)
    await db.flush()
    return turn


async def test_panel_renders_for_seated_human_on_act_turn(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, user, "alice")
        await seat_player(db, "M_0001", "bob", i=2)
        await _open_turn(db, "act")
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    assert r.status_code == 200
    html = r.text
    assert 'id="play-panel"' in html
    assert 'data-your-turn="act"' in html
    assert "Lock in my move" in html
    assert "+4 them" in html  # payoff hint
    assert "+8 mutual" in html  # the cooperation upside lives on the Help card
    assert "bob" in html  # target option present


async def test_spectator_sees_no_panel_but_sees_waiting(reset_db, client) -> None:
    async with reset_db() as db:
        human = await make_user(db, 1)
        spectator = await make_user(db, 9)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, human, "alice")
        await _open_turn(db, "act")
        await db.commit()
        spectator_id = spectator.id

    r = await client.get(LIVE, cookies=_cookies(spectator_id))
    assert r.status_code == 200
    assert 'id="play-panel"' not in r.text
    assert "Waiting on" in r.text  # additive pace indicator visible to all


async def test_cockpit_persists_between_turns_for_human(reset_db, client) -> None:
    """A seated human stays in the play cockpit during the gap between turns (no
    open turn), instead of the page dropping to the spectator view. The move form
    is replaced by a calm 'waiting' line until the next turn opens."""
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, user, "alice")
        await seat_player(db, "M_0001", "bob", i=2)
        # No _open_turn(...): the match is active but between turns.
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    assert r.status_code == 200
    html = r.text
    assert 'id="play-panel"' in html  # the cockpit stays put between turns
    assert "the next turn is about to open" in html  # the waiting state
    assert "Lock in my move" not in html  # no active move form yet
    assert "data-your-turn" not in html  # and no false "your turn" signal


async def test_spectator_sees_no_cockpit_between_turns(reset_db, client) -> None:
    """Keeping the human's cockpit alive between turns must not leak a panel to a
    non-seated spectator."""
    async with reset_db() as db:
        human = await make_user(db, 1)
        spectator = await make_user(db, 9)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, human, "alice")
        # No open turn.
        await db.commit()
        spectator_id = spectator.id

    r = await client.get(LIVE, cookies=_cookies(spectator_id))
    assert r.status_code == 200
    assert 'id="play-panel"' not in r.text


async def test_panel_shows_submitted_state(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, user, "alice")
        await _open_turn(db, "act")
        await db.commit()

    # submit, then re-fetch the live fragment
    await client.post(
        f"{VIEWER}/play/act", data={"action": "HOARD"}, cookies=_cookies(user.id)
    )
    r = await client.get(LIVE, cookies=_cookies(user.id))
    assert "Submitted — you can still change this" in r.text


async def test_talk_panel_has_pass(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, user, "alice")
        await _open_turn(db, "talk")
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    assert "data-play-pass" in r.text
    assert "say something" in r.text
    assert "data-play-counter" in r.text  # character counter wired up


async def test_act_panel_reveals_this_turns_talk(reset_db, client) -> None:
    """During act, the human sees what others said this turn — speakers and the
    silent — since the open turn isn't in the feed yet."""
    from app.models.turn import TurnMessage

    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        human = await _seat_human(db, user, "alice")
        bob = await seat_player(db, "M_0001", "bob", i=2)
        await seat_player(db, "M_0001", "cy", i=3)  # stays silent this turn
        turn = await _open_turn(db, "act")
        # bob spoke this turn; cy stayed silent; the human's own note is hidden.
        db.add(TurnMessage(turn_id=turn.id, player_id=bob.id, text="let's both help"))
        db.add(TurnMessage(turn_id=turn.id, player_id=human.id, text="my private note"))
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    html = r.text
    assert "What was just said" in html
    assert "let&#39;s both help" in html or "let's both help" in html
    assert "bob" in html
    assert 'data-target-name="bob"' in html  # the speaker is tappable to target
    # The silent are folded into one "+N stayed quiet" line (spec 018) so a
    # 10-player turn never buries the action cards below the fold.
    assert "stayed quiet" in html
    assert "my private note" not in html  # the viewer's own message isn't echoed


async def test_talk_panel_recaps_last_resolved_turn(reset_db, client) -> None:
    """When a new talk phase opens, the dock recaps the turn that just resolved —
    who did what and for how many points — so the human isn't asked to speak
    again blind to the result. The mirror of the act-phase talk reveal."""
    from app.models.turn import TurnSubmission

    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        human = await _seat_human(db, user, "alice")
        bob = await seat_player(db, "M_0001", "bob", i=2)
        cy = await seat_player(db, "M_0001", "cy", i=3)
        now = datetime.now(timezone.utc)
        # Turn 1 has resolved: bob hurt the human, cy helped the human, and the
        # human hoarded. resolved_at is set so it lands in the replay history.
        resolved = Turn(
            match_id="M_0001",
            round=1,
            turn=1,
            turn_token=generate_turn_token(),
            opened_at=now,
            deadline_at=now,
            phase="act",
            resolved_at=now,
        )
        db.add(resolved)
        await db.flush()
        db.add(
            TurnSubmission(
                turn_id=resolved.id, player_id=bob.id, action="HURT",
                target_player_id=human.id,
            )
        )
        db.add(
            TurnSubmission(
                turn_id=resolved.id, player_id=cy.id, action="HELP",
                target_player_id=human.id,
            )
        )
        db.add(TurnSubmission(turn_id=resolved.id, player_id=human.id, action="HOARD"))
        # Turn 2's talk phase is now open — the human is asked to speak again.
        db.add(
            Turn(
                match_id="M_0001",
                round=1,
                turn=2,
                turn_token=generate_turn_token(),
                opened_at=now,
                deadline_at=now + timedelta(seconds=60),
                phase="talk",
            )
        )
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    assert r.status_code == 200
    html = r.text
    assert "say something" in html  # it's the talk phase
    assert "What just happened" in html  # the recap header is present
    assert "Round 1 · Turn 1" in html  # labelled with the turn that resolved
    # The interactions are spelled out (feed_actions, highlights-first).
    assert "HURT" in html
    assert "Help" in html
    # The human's own hoard folds into the quiet count, not a separate row.
    assert "1 hoarded" in html


async def test_first_talk_turn_has_no_recap(reset_db, client) -> None:
    """The very first talk phase has nothing to recap — no turn has resolved yet —
    so the dock shows the talk box with no 'what just happened' block."""
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, user, "alice")
        await seat_player(db, "M_0001", "bob", i=2)
        await _open_turn(db, "talk")  # round 1, turn 1 — nothing resolved before it
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    assert r.status_code == 200
    assert "say something" in r.text  # the talk box is present
    assert "What just happened" not in r.text  # but no recap on the first turn


async def test_join_cta_on_scheduled_viewer(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.REGISTERING)
        await db.commit()

    r = await client.get(VIEWER, cookies=_cookies(user.id))
    assert r.status_code == 200
    # The single entrance is the join screen (where "Play as yourself" leads).
    assert "Join" in r.text
    assert f"{VIEWER}/join" in r.text


async def test_registering_viewer_shows_roster_and_confirmation(reset_db, client) -> None:
    """A pre-start match shows who's registered plus a 'you're in' confirmation for
    the seated viewer — so joining lands on a roster, not a blank feed."""
    async with reset_db() as db:
        viewer = await make_user(db, 1)
        await _match(db, state=GameState.REGISTERING)
        await _seat_human(db, viewer, "alice")
        await seat_player(db, "M_0001", "bob", i=2)
        await db.commit()

    r = await client.get(VIEWER, cookies=_cookies(viewer.id))
    assert r.status_code == 200
    assert "Registered" in r.text
    assert "bob" in r.text  # a registered opponent is listed
    assert "You're in" in r.text  # the seated viewer's confirmation


async def test_dual_seat_human_sees_cockpit(reset_db, client) -> None:
    """A user who joined as a human AND sent their own AI agent (#478) still gets
    the play cockpit. The human seat drives the controls even though the agent
    seat sorts first by name — the case that used to silently hide the panel."""
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        # Agent seat name sorts before the human seat name. This is exactly what
        # made the old single `next(...)` pick the agent and drop the controls.
        await seat_player(db, "M_0001", "aiagent", user=user)
        await _seat_human(db, user, "zoe")
        await _open_turn(db, "act")
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    assert r.status_code == 200
    html = r.text
    assert 'id="play-panel"' in html  # the cockpit renders for the human seat
    assert 'data-your-turn="act"' in html  # the human can act, not just spectate
    assert "Lock in my move" in html


async def test_dual_seat_coach_note_targets_agent(reset_db, client) -> None:
    """Saving a coach note for a dual-seat user lands on the AI agent (the seat
    with a strategy), not the human seat — and the two-seat fetch doesn't crash
    (the old `.one_or_none()` raised MultipleResultsFound for these users)."""
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        agent_player = await seat_player(db, "M_0001", "aiagent", user=user)
        human_player = await _seat_human(db, user, "zoe")
        await db.commit()
        agent_pid, human_pid = agent_player.id, human_player.id

    r = await client.post(
        f"{VIEWER}/coach-note", data={"note": "play nicer"}, cookies=_cookies(user.id)
    )
    assert r.status_code == 200  # no MultipleResultsFound crash

    async with reset_db() as db:
        agent_row = await db.get(Player, agent_pid)
        human_row = await db.get(Player, human_pid)
        assert agent_row.coach_note == "play nicer"  # coaching hit the agent seat
        assert human_row.coach_note is None  # not the human seat


async def test_autopilot_panel_shows_left_state(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        player = await _seat_human(db, user, "alice")
        player.autopilot_at = datetime.now(timezone.utc)
        await _open_turn(db, "act")
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    assert "You left this match" in r.text
    assert "Lock in my move" not in r.text  # no active form when on autopilot
