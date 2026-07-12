"""The replay renders the server's numbers, never its own scoring rules.

The animation used to carry its own copy of the payoff table (hoard +2, pact +8,
help +4, hurt -4, betrayal +4) and its own zero-floor. Both were wrong:

- A pact pays 8 in only two of the five ``MutualHelpMode`` values. Under the
  shipped default (``decay``) it slides 8, 7, 6, 5…, and ``flat_7`` / ``flat_6``
  never pay 8 at all — so the floating number and the standings rail disagreed
  with the caption and with the real score.
- The server floors a player's NET for the turn; the JS floored each hurt
  separately, which can land somewhere else entirely.

So the payload now ships the resolver's own per-turn answer and the replay only
renders it. These tests pin that contract, and the last one is the tripwire that
stops a payoff constant creeping back into the JS.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy

from app.models import GameState, Match, Player, Turn, TurnSubmission, User
from tests.factories import make_agent

REPLAY_JS = Path("app/static/rc-replay.js")


async def _seed_pact_match(reset_db, *, mode: str, tag: str = "x") -> None:
    """Two seats that mutually HELP on two turns, under *mode*.

    Point values are written exactly as a resolver would leave them so the test
    asserts on the shipping contract, not on a re-derivation of the rules. ``tag``
    keeps user identities unique when one test seeds several matches in turn.
    """
    async with reset_db() as db:
        users, players = [], []
        for i in range(2):
            u = User(google_sub=f"pact-{tag}-u{i}", email=f"pact-{tag}-{i}@t.com")
            db.add(u)
            await db.flush()
            users.append(u)
        match = Match(
            id="G_001",
            name="Pact",
            state=GameState.COMPLETED,
            scheduled_start=datetime.now(timezone.utc),
            current_round=1,
            current_turn=2,
            mutual_help_mode=mode,
        )
        db.add(match)
        await db.flush()
        for i, u in enumerate(users):
            agent, version = await make_agent(db, u, name=f"AI_{i}")
            p = Player(
                match_id="G_001",
                user_id=u.id,
                agent_id=agent.id,
                seat_name=f"AI_{i}",
                agent_version_id=version.id if version is not None else None,
                model_self_report=version.model if version is not None else None,
            )
            db.add(p)
            await db.flush()
            players.append(p)

        # Turn 1: first pact pays 8 each. Turn 2: the same pair repeats, so under
        # `decay` it pays 7 each — the case the old hardcoded 8 got wrong.
        for turn_no, (per_side, running) in enumerate([(8, 8), (7, 15)], start=1):
            t = Turn(
                match_id="G_001",
                round=1,
                turn=turn_no,
                turn_token=f"tk{turn_no}",
                opened_at=datetime.now(timezone.utc),
                deadline_at=datetime.now(timezone.utc),
                resolved_at=datetime.now(timezone.utc),
            )
            db.add(t)
            await db.flush()
            for me, them in ((players[0], players[1]), (players[1], players[0])):
                db.add(
                    TurnSubmission(
                        turn_id=t.id,
                        player_id=me.id,
                        action="HELP",
                        target_player_id=them.id,
                        message="pact",
                        points_delta=per_side,
                        round_score_after=running,
                        submitted_at=datetime.now(timezone.utc),
                    )
                )
        await db.commit()


async def _rc_data(client) -> dict:
    """The replay payload the page ships to the browser."""
    r = await client.get("/games/hoard-hurt-help/matches/G_001")
    assert r.status_code == 200
    start = r.text.index('id="rc-data"')
    blob = r.text[r.text.index(">", start) + 1 : r.text.index("</script>", start)]
    return json.loads(blob)


async def test_payload_ships_the_servers_running_score(client, reset_db) -> None:
    """Every action carries `score_after`, so the rail never does its own maths."""
    await _seed_pact_match(reset_db, mode="decay")
    data = await _rc_data(client)

    actions = [a for t in data["turns"] for a in t["actions"]]
    assert actions, "expected the seeded turns to reach the replay payload"
    for a in actions:
        assert "score_after" in a, f"action missing score_after: {a}"
        assert isinstance(a["score_after"], int)

    async with reset_db() as db:
        persisted = sorted(
            (await db.execute(sqlalchemy.select(TurnSubmission.round_score_after)))
            .scalars()
            .all()
        )
    assert sorted(a["score_after"] for a in actions) == persisted


async def test_a_repeated_pact_ships_its_decayed_value(client, reset_db) -> None:
    """The bug: the second pact really pays 7, and the replay used to show 8."""
    await _seed_pact_match(reset_db, mode="decay")
    data = await _rc_data(client)

    by_turn = {t["turn"]: t["actions"] for t in data["turns"]}
    assert {a["delta"] for a in by_turn[1]} == {8}
    assert {a["delta"] for a in by_turn[2]} == {7}, (
        "a repeated pact must ship its decayed value, not a flat 8"
    )


async def test_every_shipped_rule_set_reaches_the_replay(client, reset_db) -> None:
    """The point of the change: the replay follows whatever rule the match uses.

    Runs the payload for EVERY ``MutualHelpMode`` and asserts the second pact's
    shipped value matches what ``mutual_help_value`` says that mode pays. The old
    animation showed 8 for all of them, so three of the five rendered wrong. This
    test needs no update when a sixth mode is added — it enumerates the enum.
    """
    from app.games.hoard_hurt_help.rules import MutualHelpMode, mutual_help_value

    shipped: dict[str, tuple[int, int]] = {}
    for mode in MutualHelpMode:
        await _seed_pact_match(reset_db, mode=mode.value, tag=mode.value)
        by_turn = {t["turn"]: t["actions"] for t in (await _rc_data(client))["turns"]}

        first = {a["delta"] for a in by_turn[1]}
        repeat = {a["delta"] for a in by_turn[2]}
        assert len(first) == 1 and len(repeat) == 1, mode.value
        shipped[mode.value] = (first.pop(), repeat.pop())

        # Same seeded actions every time — only the match's mode differs — so the
        # shipped value must come from the rules module, not the seed data.
        assert shipped[mode.value][0] == mutual_help_value(mode, 0), mode.value
        assert shipped[mode.value][1] == mutual_help_value(
            mode, 1, repeated_last_turn=True
        ), mode.value

        async with reset_db() as db:
            for table in ("turn_submissions", "turns", "players", "matches"):
                await db.execute(sqlalchemy.text(f"DELETE FROM {table}"))
            await db.commit()

    # And the modes really do differ — otherwise the assertions above could all
    # pass against a client that still hardcoded one number.
    assert len(set(shipped.values())) > 1, f"expected the modes to differ: {shipped}"


def test_bundled_sample_replay_carries_scores() -> None:
    """The homepage fallback is a recording made before `score_after` existed.

    It is a real user-visible path, so without back-filling it the front page's
    replay would render blank scores — the regression this pins.
    """
    from app.games.hoard_hurt_help.viewer import sample_replay_data

    payload = json.loads(sample_replay_data())
    actions = [a for t in payload["turns"] for a in t["actions"]]
    assert actions
    assert all(isinstance(a.get("score_after"), int) for a in actions)
    # Somebody has to be on the board, or we back-filled zeroes.
    assert any(a["score_after"] > 0 for a in actions)


async def test_replay_js_holds_no_payoff_constants() -> None:
    """Tripwire. The rules live in app/games/hoard_hurt_help/rules.py and are
    applied by the resolver; the replay must render shipped numbers only.

    Any of these patterns means someone re-implemented scoring in JavaScript,
    which is exactly how the animation drifted from the real scores.
    """
    source = REPLAY_JS.read_text()
    banned = {
        "adds a hardcoded payoff": ["(sim[a.agent]||0)+", "(rScore[a.agent]||0)+", "(rScore[a.target]||0)+"],
        "shows a hardcoded delta": ["showDelta(el, 2)", "showDelta(el, 4)", "showDelta(el, 8)",
                                    "showDelta(T, 4)", "showDelta(T, 8)", "showDelta(T, -4)"],
        "re-floors the score itself": ["Math.max(0,(rScore", "Math.max(0,(sim"],
    }
    offenders = [
        f"{why}: {pattern}"
        for why, patterns in banned.items()
        for pattern in patterns
        if pattern in source
    ]
    assert not offenders, "replay JS re-implements scoring:\n  " + "\n  ".join(offenders)
