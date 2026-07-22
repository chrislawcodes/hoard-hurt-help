"""Slice 3 — the play panel renders for the seated human only, with the right state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


from app.engine.human_player import get_or_create_human_agent
from app.engine.tokens import generate_turn_token
from app.models import GameState, Match, Player, User
from app.models.turn import Turn
from tests.factories import make_user, seat_player
from tests.conftest import signed_in_cookies as _cookies

GAME = "hoard-hurt-help"
VIEWER = f"/games/{GAME}/matches/M_0001"
LIVE = f"{VIEWER}/live"


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
    assert "You can still change this" in r.text  # submitted state, dock controls-only


async def test_talk_panel_has_pass(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, user, "alice")
        await _open_turn(db, "talk")
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    assert "data-play-pass" in r.text
    assert "Stay quiet" in r.text  # talk-phase control (the dock title was cut)
    assert "data-play-counter" in r.text  # character counter wired up


async def test_talk_submit_keeps_message_and_shows_sent_state(reset_db, client) -> None:
    """After sending a talk message the panel must re-render with the message kept
    in the box and an obviously-submitted look — not an empty box that reads as
    'nothing sent' (the bug). Mirrors how the act phase keeps the chosen action."""
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, user, "alice")
        await seat_player(db, "M_0001", "bob", i=2)
        await _open_turn(db, "talk")
        await db.commit()

    msg = "trust me this round"
    submit = await client.post(
        f"{VIEWER}/play/talk", data={"message": msg}, cookies=_cookies(user.id)
    )
    assert submit.status_code == 200
    # The POST returns the freshly-rendered live region; assert on it directly so
    # we cover the exact HTML the user sees right after pressing Send.
    html = submit.text
    assert f'value="{msg}"' in html  # the message is kept in the box
    assert "play-msg-sent" in html  # the box carries the submitted look
    assert "✓ Sent" in html  # status reads as a confirmation
    assert ">Update<" in html  # Send becomes Update once a message is in
    # A plain GET of the live region must show the same kept state (the panel is
    # re-rendered the same way on every poll, not just on the POST response).
    r = await client.get(LIVE, cookies=_cookies(user.id))
    assert f'value="{msg}"' in r.text


async def test_talk_pass_shows_staying_quiet_not_sent(reset_db, client) -> None:
    """Staying quiet (an empty submit) is a real submitted state too, but it must
    not claim 'Sent' over an empty box — it reads 'Staying quiet' instead."""
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, user, "alice")
        await seat_player(db, "M_0001", "bob", i=2)
        await _open_turn(db, "talk")
        await db.commit()

    submit = await client.post(
        f"{VIEWER}/play/talk", data={"message": ""}, cookies=_cookies(user.id)
    )
    assert submit.status_code == 200
    html = submit.text
    assert "✓ Staying quiet" in html
    assert "✓ Sent" not in html
    assert "play-msg-sent" not in html  # no green-box treatment for an empty pass


async def test_started_match_feed_shows_roster(reset_db, client) -> None:
    """At game start (active, first turn open, nothing resolved yet) the feed shows
    who's playing — a roster of the seated players — instead of a bare 'waiting'
    line, so a fresh table isn't a mystery."""
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, user, "alice")
        await seat_player(db, "M_0001", "bob", i=2)
        await _open_turn(db, "talk")  # first turn open; no turn resolved yet
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    html = r.text
    assert "feed-roster" in html
    assert "players — waiting for the first move" in html
    assert "bob" in html  # an opponent is listed by name
    assert "No turns resolved yet" not in html  # the bare line is replaced


async def test_act_phase_reveals_this_turns_talk_in_feed(reset_db, client) -> None:
    """During act, the human sees what everyone said this turn — their own line
    plus other speakers and the silent — as the live top card of the one feed
    (spec 019), not a box in the dock. The open turn isn't in the resolved feed
    yet, so this card carries it."""
    from app.models.turn import TurnMessage

    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        human = await _seat_human(db, user, "alice")
        bob = await seat_player(db, "M_0001", "bob", i=2)
        await seat_player(db, "M_0001", "cy", i=3)  # stays silent this turn
        turn = await _open_turn(db, "act")
        # bob spoke this turn; cy stayed silent; the human also spoke.
        db.add(TurnMessage(turn_id=turn.id, player_id=bob.id, text="let's both help"))
        db.add(TurnMessage(turn_id=turn.id, player_id=human.id, text="my own note"))
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    html = r.text
    assert "This turn — what everyone said" in html  # the live feed card header
    assert "turn-live" in html  # rendered as the feed's top card, not a dock box
    assert "let&#39;s both help" in html or "let's both help" in html
    assert "bob" in html
    assert 'data-target-name="bob"' in html  # the speaker is tappable to target
    # The silent are folded into one "+N stayed quiet" line (spec 018) so a
    # 10-player turn never buries the action cards below the fold.
    assert "stayed quiet" in html
    # The viewer's own line IS shown now — they shouldn't be the one missing
    # player — accent-marked as "you" and NOT tappable (you can't target yourself).
    assert "my own note" in html
    assert "who-name is-you" in html
    assert 'data-target-name="alice"' not in html


async def test_act_reveal_shows_you_stayed_quiet_when_viewer_silent(reset_db, client) -> None:
    """If the human stayed quiet, their own line still appears (as 'you stayed
    quiet') — they're always represented in the turn's talk, not dropped."""
    from app.models.turn import TurnMessage

    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.ACTIVE)
        await _seat_human(db, user, "alice")
        bob = await seat_player(db, "M_0001", "bob", i=2)
        turn = await _open_turn(db, "act")
        db.add(TurnMessage(turn_id=turn.id, player_id=bob.id, text="trust me"))
        await db.commit()

    r = await client.get(LIVE, cookies=_cookies(user.id))
    html = r.text
    assert "you stayed quiet" in html  # the viewer's own silent line is shown
    assert "who-name is-you" in html
    assert "trust me" in html  # the opponent who spoke is still shown


async def test_talk_phase_shows_last_result_in_feed_not_dock(reset_db, client) -> None:
    """When a new talk phase opens, the just-resolved turn shows in the FEED as its
    top card — not as a recap box in the dock. Spec 019 makes the dock controls
    only; the human reads the result from the one feed before speaking again."""
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
    assert "Stay quiet" in html  # it's the talk phase
    assert "What just happened" not in html  # no dock recap (spec 019)
    assert "Turn 1" in html  # the just-resolved turn shows in the feed (cards read "Turn N")


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
    assert "Stay quiet" in r.text  # talk-phase control (the dock title was cut)  # the talk box is present
    assert "What just happened" not in r.text  # but no recap on the first turn


async def test_join_cta_on_scheduled_viewer(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        await _match(db, state=GameState.REGISTERING)
        await db.commit()

    r = await client.get(VIEWER, cookies=_cookies(user.id))
    assert r.status_code == 200
    # The single entrance is the join screen (where "Play manually" leads).
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
