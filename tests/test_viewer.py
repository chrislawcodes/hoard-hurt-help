"""Match viewer + SSE + spectator API tests."""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.models import (
    Match,
    GameState,
    Player,
    Turn,
    TurnMessage,
    TurnSubmission,
)
from tests.factories import make_agent, make_match, make_user, seat_prebuilt_player

REPO_ROOT = Path(__file__).resolve().parents[1]


async def _seed(reset_db, state=GameState.ACTIVE, *, scheduled_start=None, match_kind="manual"):
    async with reset_db() as db:
        u = await make_user(db)
        match = await make_match(
            db,
            "G_001",
            state=state,
            name="Test",
            scheduled_start=scheduled_start or datetime.now(timezone.utc),
            match_kind=match_kind,
            current_round=1,
            current_turn=1,
        )
        agent, version = await make_agent(db, u, name="AI_0")
        version.strategy_text = "SECRET STRATEGY DO NOT LEAK"
        await seat_prebuilt_player(
            db, match=match, user=u, agent=agent, version=version, seat_name="AI_0"
        )
        await db.commit()


async def _seed_two_phase_turn(
    reset_db,
    *,
    include_turn_messages: bool = True,
    talk_thinking: str = "private talk reasoning",
    act_thinking: str = "private act reasoning",
    talk_text: str = "public talk",
    legacy_message: str = "legacy public chat",
):
    async with reset_db() as db:
        player = (
            await db.execute(select(Player).where(Player.match_id == "G_001"))
        ).scalars().first()
        assert player is not None
        turn = Turn(
            match_id="G_001",
            round=1,
            turn=1,
            turn_token="tk1",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
            phase="act",
            resolved_at=datetime.now(timezone.utc),
        )
        db.add(turn)
        await db.flush()
        if include_turn_messages:
            db.add(
                TurnMessage(
                    turn_id=turn.id,
                    player_id=player.id,
                    text=talk_text,
                    thinking=talk_thinking,
                    was_defaulted=False,
                    submitted_at=datetime.now(timezone.utc),
                )
            )
        db.add(
            TurnSubmission(
                turn_id=turn.id,
                player_id=player.id,
                action="HOARD",
                message=legacy_message,
                thinking=act_thinking,
                points_delta=2,
                round_score_after=2,
                was_defaulted=False,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()


async def test_viewer_renders_active(client, reset_db):
    await _seed(reset_db, GameState.ACTIVE)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert "Test" in r.text


async def test_viewer_does_not_leak_strategy(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert "SECRET STRATEGY" not in r.text


async def test_viewer_renders_talk_then_act_and_thinking(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    await _seed_two_phase_turn(reset_db)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert "action-card hoard" in r.text
    assert "public talk" in r.text
    assert "Hoard" in r.text
    assert "+2" in r.text
    assert "private talk reasoning" in r.text
    assert "private act reasoning" in r.text
    # Thinking is shown to humans, paired with each move (no longer a closed toggle).
    assert 'class="thought"' in r.text


async def test_legacy_viewer_falls_back_to_submission_message(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    await _seed_two_phase_turn(reset_db, include_turn_messages=False)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert "legacy public chat" in r.text


async def test_live_fragment_carries_replay_data(client, reset_db):
    """The SSE-refreshed live fragment must embed fresh replay JSON.

    The robot-circle animation is rendered once at page load and lives outside
    the live region, so it can only learn about new turns from the #rc-data-live
    blob each /live swap brings. Without it, an open page freezes the replay at
    the turn count it loaded with (the bug this guards against).
    """
    import json

    await _seed(reset_db, GameState.ACTIVE)
    await _seed_two_phase_turn(reset_db)
    r = await client.get("/games/hoard-hurt-help/matches/G_001/live")
    assert r.status_code == 200
    assert 'id="rc-data-live"' in r.text
    start = r.text.index('id="rc-data-live"')
    blob = r.text[r.text.index(">", start) + 1 : r.text.index("</script>", start)]
    data = json.loads(blob)
    assert [(t["round"], t["turn"]) for t in data["turns"]] == [(1, 1)]


async def test_spectator_state_no_prompts(client, reset_db):
    await _seed(reset_db, GameState.ACTIVE)
    r = await client.get("/api/spectator/games/G_001/state")
    assert r.status_code == 200
    body = r.json()
    # Schema has no strategy field; verify by absence.
    assert "strategy_prompt" not in r.text
    assert body["name"] == "Test"


async def test_spectator_state_two_phase_shape_without_thinking(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    await _seed_two_phase_turn(reset_db)
    r = await client.get("/api/spectator/games/G_001/state")
    assert r.status_code == 200
    body = r.json()
    assert "thinking" not in r.text
    assert "private talk reasoning" not in r.text
    assert "private act reasoning" not in r.text
    assert body["history"] == [
        {
            "round": 1,
            "turn": 1,
            "messages": [
                {
                    "agent_id": "AI_0",
                    "message": "public talk",
                }
            ],
            "actions": [
                {
                    "agent_id": "AI_0",
                    "action": "HOARD",
                    "target_id": None,
                    "quantity": None,
                    "face": None,
                    "points_delta": 2,
                }
            ],
        }
    ]


async def test_completed_viewer_has_round_nav(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    # A completed game needs at least one resolved turn for the round nav to show.
    async with reset_db() as db:
        from app.models import Player, Turn, TurnSubmission

        p = (await db.execute(__import__("sqlalchemy").select(Player))).scalars().first()
        t = Turn(
            match_id="G_001",
            round=1,
            turn=1,
            turn_token="tk1",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
            resolved_at=datetime.now(timezone.utc),
        )
        db.add(t)
        await db.flush()
        db.add(
            TurnSubmission(
                turn_id=t.id,
                player_id=p.id,
                action="HOARD",
                message="hi",
                points_delta=2,
                round_score_after=2,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    # Round-jump bar and grouped round section are present.
    assert "round-nav" in r.text
    assert 'data-round="1"' in r.text
    assert "round-section" in r.text
    # Match replay should start on its own for spectators: the engine is a
    # static file (rc-replay.js) that reads the server's autoplay choice from
    # data-autoplay on the #rc-data island.
    assert '<script src="/static/rc-replay.js"></script>' in r.text
    assert 'id="rc-data" data-autoplay="true"' in r.text
    engine = (REPO_ROOT / "app" / "static" / "rc-replay.js").read_text()
    assert "dataset.autoplay==='true'" in engine


def test_replay_script_fragment_carries_autoplay_flag():
    """Pin the template<->engine contract left after extracting the replay JS
    to app/static/rc-replay.js: the fragment renders the #rc-data island with
    data-autoplay reflecting rc_autoplay (default false), loads the engine via
    a plain synchronous <script src> (no defer/async — execution order matters,
    see _replay_script.html), and the static file itself is template-free."""
    from app.templating import templates

    tpl = templates.env.get_template("fragments/robot_circle/_replay_script.html")
    on = tpl.render(rc_data="{}", rc_autoplay=True)
    off = tpl.render(rc_data="{}")
    assert 'id="rc-data" data-autoplay="true"' in on
    assert 'id="rc-data" data-autoplay="false"' in off
    for html in (on, off):
        assert '<script src="/static/rc-replay.js"></script>' in html
        assert "defer" not in html and "async" not in html
    engine = (REPO_ROOT / "app" / "static" / "rc-replay.js").read_text()
    assert "{{" not in engine and "{%" not in engine
    assert "dataset.autoplay==='true'" in engine


async def test_viewer_shows_per_move_effect_on_target(client, reset_db):
    """A HURT row must show the loss on the TARGET, not just the actor's +0."""
    await _seed(reset_db, GameState.COMPLETED)
    async with reset_db() as db:
        import sqlalchemy

        from app.models import Player, Turn, TurnSubmission

        actor = (await db.execute(sqlalchemy.select(Player))).scalars().first()
        match = (
            await db.execute(sqlalchemy.select(Match).where(Match.id == "G_001"))
        ).scalar_one()
        # Second player to be the HURT target.
        u2 = await make_user(db, 2)
        bot2, version2 = await make_agent(db, u2, name="AI_1")
        target = await seat_prebuilt_player(
            db, match=match, user=u2, agent=bot2, version=version2, seat_name="AI_1"
        )
        t = Turn(
            match_id="G_001",
            round=1,
            turn=1,
            turn_token="tk1",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
            resolved_at=datetime.now(timezone.utc),
        )
        db.add(t)
        await db.flush()
        # Actor HURTs the target. Actor's own net is 0; the -4 lands on the target.
        db.add(
            TurnSubmission(
                turn_id=t.id,
                player_id=actor.id,
                action="HURT",
                target_player_id=target.id,
                message="take that",
                points_delta=0,
                round_score_after=0,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    # The target and its loss are shown; the actor's own +0 is omitted because
    # the compact action line focuses on who the move lands on.
    assert "AI_1" in r.text
    assert "-4" in r.text
    assert "+0" not in r.text


async def test_viewer_shows_attacker_bonus_on_betrayal(client, reset_db):
    """Betraying a helper must render the attacker's +4 in the feed, not just the
    victim's -4 (R4 guard — the +4 must reach the screen, not sit in the payload).

    A HURTs B while B HELPs A (same turn) → A betrays the helper: the feed shows
    the attacker's `+4 betrayal` chip. Under 8/4 the victim's chip is -4 (never -8).
    """
    await _seed(reset_db, GameState.COMPLETED)
    async with reset_db() as db:
        import sqlalchemy

        from app.models import Player, Turn, TurnSubmission

        attacker = (await db.execute(sqlalchemy.select(Player))).scalars().first()
        match = (
            await db.execute(sqlalchemy.select(Match).where(Match.id == "G_001"))
        ).scalar_one()
        u2 = await make_user(db, 2)
        bot2, version2 = await make_agent(db, u2, name="AI_1")
        victim = await seat_prebuilt_player(
            db, match=match, user=u2, agent=bot2, version=version2, seat_name="AI_1"
        )
        t = Turn(
            match_id="G_001",
            round=1,
            turn=1,
            turn_token="tk1",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
            resolved_at=datetime.now(timezone.utc),
        )
        db.add(t)
        await db.flush()
        # Attacker HURTs the victim; the victim HELPs the attacker the same turn.
        db.add(
            TurnSubmission(
                turn_id=t.id, player_id=attacker.id, action="HURT",
                target_player_id=victim.id, message="thanks for the help",
                points_delta=8, round_score_after=8,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            TurnSubmission(
                turn_id=t.id, player_id=victim.id, action="HELP",
                target_player_id=attacker.id, message="here you go",
                points_delta=0, round_score_after=0,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    # The attacker's +4 betrayal bonus is rendered (not buried) ...
    assert "+4 betrayal" in r.text
    # ... and the victim's delta chip is the normal -4, never a stale -8. Match the
    # rendered delta span content specifically (a bare "-8" substring false-matches
    # "utf-8" in the page <head>).
    assert ">-8<" not in r.text
    assert ">-4<" in r.text


async def test_guide_serves_doc(client, reset_db):
    r = await client.get("/guide/setup-mcp")
    assert r.status_code == 200
    assert "claude mcp add" in r.text


async def test_guide_rejects_unknown_and_traversal(client, reset_db):
    assert (await client.get("/guide/nonexistent")).status_code == 404
    assert (await client.get("/guide/..%2f..%2fetc%2fpasswd")).status_code == 404


async def test_list_games_public(client, reset_db):
    """GET /api/games returns a JSON list of all games."""
    await _seed(reset_db, GameState.ACTIVE)
    r = await client.get("/api/games")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["id"] == "G_001"
    assert body[0]["state"] == "active"
    assert body[0]["player_count"] == 1
    assert "strategy_prompt" not in r.text  # no leak


async def test_list_games_public_filter_by_state(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    r = await client.get("/api/games?state=active")
    assert r.status_code == 200
    assert r.json() == []
    r2 = await client.get("/api/games?state=completed")
    assert len(r2.json()) == 1


async def test_scheduled_viewer_shows_start_countdown(client, reset_db):
    """A waiting match shows a start-countdown band below the robot stage."""
    start = datetime(2099, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    await _seed(reset_db, GameState.SCHEDULED, scheduled_start=start)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert 'id="rc-countdown"' in r.text
    # The clock counts down to the match's scheduled start time.
    assert 'data-start="2099-01-02T03:04:05' in r.text


async def test_registering_viewer_shows_start_countdown(client, reset_db):
    await _seed(reset_db, GameState.REGISTERING)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert 'id="rc-countdown"' in r.text


async def test_active_viewer_has_no_start_countdown(client, reset_db):
    await _seed(reset_db, GameState.ACTIVE)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert 'id="rc-countdown"' not in r.text


async def test_completed_viewer_has_no_start_countdown(client, reset_db):
    await _seed(reset_db, GameState.COMPLETED)
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert 'id="rc-countdown"' not in r.text


async def test_practice_arena_has_no_start_countdown(client, reset_db):
    """A practice arena starts on join (no fixed time), so it gets no clock."""
    await _seed(reset_db, GameState.SCHEDULED, match_kind="practice_arena")
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    assert 'id="rc-countdown"' not in r.text


async def test_replay_history_carries_per_turn_score_that_resets_each_round():
    """The feed must show each turn's OWN in-round score, not one live score.

    Regression: the transcript stamped every turn with each player's current
    round score, so once a new round reset scores toward 0 the whole transcript
    (old rounds included) showed those low numbers — the points looked lost.
    Each history turn now carries `score_after` (the round score as of that
    turn), which climbs within a round and resets at the next round's start.
    """
    from app.games.hoard_hurt_help.viewer import build_pd_replay_view
    from app.read_models.matches import TimelineAction, TimelineTurn

    players = [Player(seat_name="AI_0"), Player(seat_name="AI_1")]

    def hoard(seat: str, score_after: int) -> TimelineAction:
        return TimelineAction(
            agent_id=seat,
            action="HOARD",
            target_id=None,
            quantity=None,
            face=None,
            message="",
            thinking="",
            points_delta=2,
            round_score_after=score_after,
            submitted_at=datetime.now(timezone.utc),
            was_defaulted=False,
        )

    timeline = [
        # Round 1: AI_0 banks two HOARDs (2 then 4).
        TimelineTurn(round=1, turn=1, messages=[], actions=[hoard("AI_0", 2), hoard("AI_1", 2)]),
        TimelineTurn(round=1, turn=2, messages=[], actions=[hoard("AI_0", 4), hoard("AI_1", 4)]),
        # Round 2: scores reset, so turn 1 of round 2 is back to 2.
        TimelineTurn(round=2, turn=1, messages=[], actions=[hoard("AI_0", 2), hoard("AI_1", 2)]),
    ]

    view = await build_pd_replay_view(
        db=None,  # build_pd_replay_view reads only the passed-in rows
        match=Match(id="G_001", game="hoard-hurt-help", turns_per_round=7),
        players=players,
        scoreboard=[
            {"agent_id": "AI_0", "round_score": 2, "round_wins": 0, "provider": None},
            {"agent_id": "AI_1", "round_score": 2, "round_wins": 0, "provider": None},
        ],
        timeline=timeline,
        viewer_seat="AI_0",
    )
    history = view["history"]
    assert [h["score_after"]["AI_0"] for h in history] == [2, 4, 2]
    # The round-2 reset turn shows 2, not the round-1 peak of 4.
    assert history[-1]["score_after"] == {"AI_0": 2, "AI_1": 2}


async def test_rc_data_ships_delta_and_betrayal_bonus_on_every_action():
    """Pin the payload contract the replay JS relies on (it no longer owns a
    payoff table — see app/static/rc-replay.js's actionDelta): every rc_data action
    ships an integer `delta` — HOARD's +2 to the actor, the pact's DECAYED
    per-side value on each half, the one-way HELP's +4 to the target, the HURT's
    nominal -4 on the victim — and a HURT that betrays a same-turn helper ships
    the attacker's gain on `betrayal_bonus`, never on `delta`.
    """
    import json

    from app.games.hoard_hurt_help.viewer import build_pd_replay_view
    from app.read_models.matches import TimelineAction, TimelineTurn

    players = [Player(seat_name="AI_0"), Player(seat_name="AI_1")]

    def act(seat: str, action: str, target: str | None, score_after: int) -> TimelineAction:
        return TimelineAction(
            agent_id=seat,
            action=action,
            target_id=target,
            quantity=None,
            face=None,
            message="",
            thinking="",
            points_delta=0,
            round_score_after=score_after,
            submitted_at=datetime.now(timezone.utc),
            was_defaulted=False,
        )

    timeline = [
        # Turns 1-2: the same pair pacts twice — fresh +8 each, then decayed +7.
        TimelineTurn(round=1, turn=1, messages=[], actions=[
            act("AI_0", "HELP", "AI_1", 8), act("AI_1", "HELP", "AI_0", 8)]),
        TimelineTurn(round=1, turn=2, messages=[], actions=[
            act("AI_0", "HELP", "AI_1", 15), act("AI_1", "HELP", "AI_0", 15)]),
        # Turn 3: AI_0 HURTs its same-turn helper (betrayal) — AI_1's HELP is one-way.
        TimelineTurn(round=1, turn=3, messages=[], actions=[
            act("AI_0", "HURT", "AI_1", 23), act("AI_1", "HELP", "AI_0", 11)]),
        # Turn 4: plain hoards.
        TimelineTurn(round=1, turn=4, messages=[], actions=[
            act("AI_0", "HOARD", None, 25), act("AI_1", "HOARD", None, 13)]),
    ]

    view = await build_pd_replay_view(
        db=None,  # build_pd_replay_view reads only the passed-in rows
        match=Match(id="G_001", game="hoard-hurt-help", turns_per_round=7),
        players=players,
        scoreboard=[
            {"agent_id": "AI_0", "round_score": 25, "round_wins": 0, "provider": None},
            {"agent_id": "AI_1", "round_score": 13, "round_wins": 0, "provider": None},
        ],
        timeline=timeline,
        viewer_seat="AI_0",
    )
    rc = json.loads(view["rc_data"])
    for turn in rc["turns"]:
        for a in turn["actions"]:
            assert isinstance(a["delta"], int)  # the JS consumes this directly
    # Fresh pact pays +8 per side; the repeat decays to +7 (match-wide counter).
    assert [a["delta"] for a in rc["turns"][0]["actions"]] == [8, 8]
    assert [a["delta"] for a in rc["turns"][1]["actions"]] == [7, 7]
    # Betrayal HURT: the victim's nominal -4 rides `delta` (the running-score
    # clamp at 0 stays client-side); the attacker's +4 rides `betrayal_bonus`.
    hurt = next(a for a in rc["turns"][2]["actions"] if a["action"] == "HURT")
    assert hurt["delta"] == -4
    assert hurt["betrayed_helper"] is True
    assert hurt["betrayal_bonus"] == 4
    # One-way HELP lands on the target; HOARD credits the actor.
    one_way = next(a for a in rc["turns"][2]["actions"] if a["action"] == "HELP")
    assert one_way["delta"] == 4
    assert [a["delta"] for a in rc["turns"][3]["actions"]] == [2, 2]
