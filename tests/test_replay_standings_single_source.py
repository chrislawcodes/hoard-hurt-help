"""One ranker: the server. The replay rail only draws what it is handed.

Two bugs sat in ``app/static/rc-replay.js``:

- It sorted the standings rail by in-round points first and rounds won second.
  Every ranking on the server is the other way round (``rank_standings``,
  ``finish_order_sort_key``). A completed match renders the final scoreboard and
  the rail on the same page, so the two lists openly disagreed — a seat with two
  round-wins and no points led one and sat below a no-win, high-points seat in
  the other.
- It re-derived the round-win award (top scorer takes the round, a tie splits
  it), a second copy of ``award_round_winners``' rule.

Now the payload carries the ranked standings for every turn, the award rule lives
in one pure function both paths call, and these tests pin both.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from itertools import permutations
from typing import Any

import pytest

from app.engine.resolver import award_round_winners, round_award
from app.games.replay_standings import stamp_turn_standings
from app.models import GameState, Match, Player, Turn, TurnSubmission, User
from tests.factories import make_agent

MATCH_ID = "G_STAND"


async def _seed_match(
    reset_db,
    *,
    rounds: list[list[tuple[str, str, str | None, int]]],
    totals: dict[str, tuple[float, int, int]],
    winner_seat: str,
) -> None:
    """Seed a finished match, one turn per round.

    ``rounds`` is a list of rounds; each round is a list of
    ``(seat, action, target_seat, round_score_after)`` — written the way the
    resolver would have left them, so the test asserts on the shipping contract
    rather than on a re-derivation of the scoring rules. ``totals`` maps a seat to
    its final ``(round_wins, total_round_score, current_round_score)``: the
    server's own tally, which is what the standings must agree with.
    """
    seats = list(totals)
    async with reset_db() as db:
        match = Match(
            id=MATCH_ID,
            name="Standings",
            state=GameState.COMPLETED,
            scheduled_start=datetime.now(timezone.utc),
            total_rounds=len(rounds),
            turns_per_round=1,
            current_round=len(rounds),
            current_turn=1,
            rounds_awarded=len(rounds),
        )
        db.add(match)
        await db.flush()

        player_by_seat: dict[str, Player] = {}
        for i, seat in enumerate(seats):
            user = User(google_sub=f"stand-u{i}", email=f"stand-{i}@t.com")
            db.add(user)
            await db.flush()
            agent, version = await make_agent(db, user, name=seat)
            wins, total_score, round_score = totals[seat]
            player = Player(
                match_id=MATCH_ID,
                user_id=user.id,
                agent_id=agent.id,
                seat_name=seat,
                agent_version_id=version.id if version is not None else None,
                total_round_wins=wins,
                total_round_score=total_score,
                current_round_score=round_score,
            )
            db.add(player)
            await db.flush()
            player_by_seat[seat] = player

        for round_no, submissions in enumerate(rounds, start=1):
            turn = Turn(
                match_id=MATCH_ID,
                round=round_no,
                turn=1,
                turn_token=f"tk{round_no}",
                opened_at=datetime.now(timezone.utc),
                deadline_at=datetime.now(timezone.utc),
                resolved_at=datetime.now(timezone.utc),
            )
            db.add(turn)
            await db.flush()
            for seat, action, target, score_after in submissions:
                db.add(
                    TurnSubmission(
                        turn_id=turn.id,
                        player_id=player_by_seat[seat].id,
                        action=action,
                        target_player_id=player_by_seat[target].id if target else None,
                        message="",
                        points_delta=score_after,
                        round_score_after=score_after,
                        submitted_at=datetime.now(timezone.utc),
                    )
                )

        match.winner_player_id = player_by_seat[winner_seat].id
        await db.commit()


async def _page(client) -> tuple[str, dict[str, Any]]:
    """The rendered match page and the replay payload embedded in it."""
    response = await client.get(f"/games/hoard-hurt-help/matches/{MATCH_ID}")
    assert response.status_code == 200
    html = response.text
    start = html.index('id="rc-data"')
    blob = html[html.index(">", start) + 1 : html.index("</script>", start)]
    return html, json.loads(blob)


async def _seed_wins_beat_points(reset_db) -> None:
    """Zeno takes two rounds on the board; Ajax takes the last one and the points.

    Every turn is one HOARD (+2) plus one one-way HELP (+4 to the target), so the
    scores are real payouts. Zeno finishes with 2 round-wins and 0 points on the
    board; Ajax with 1 round-win and 6 — the exact shape the old JS got backwards.
    Seat names are picked so alphabetical order is NOT the right answer either.
    """
    await _seed_match(
        reset_db,
        rounds=[
            [("Zeno", "HOARD", None, 6), ("Ajax", "HELP", "Zeno", 0)],
            [("Zeno", "HOARD", None, 6), ("Ajax", "HELP", "Zeno", 0)],
            [("Ajax", "HOARD", None, 6), ("Zeno", "HELP", "Ajax", 0)],
        ],
        totals={"Ajax": (1.0, 6, 6), "Zeno": (2.0, 12, 0)},
        winner_seat="Zeno",
    )


# --- The regression: the rail and the final scoreboard are one list -----------


async def test_rail_finishes_in_the_final_scoreboards_order(client, reset_db) -> None:
    """The bug, end to end: both lists render on the same page and must agree.

    The payload's last-turn standings are compared against the final scoreboard
    the server rendered beside them — same match, same page, one order.
    """
    await _seed_wins_beat_points(reset_db)
    html, data = await _page(client)

    rail = data["turns"][-1]["standings"]["after"]
    rail_names = [data["labels"][row["agent"]] for row in rail]
    final_names = re.findall(r'class="gf-nm">([^<]+)<', html)

    assert final_names, "expected the completed match to render its final scoreboard"
    assert rail_names == final_names
    # And it is the wins-first answer, not the points-first one the JS used to
    # compute: Zeno leads on 2 round-wins with nothing on the board.
    assert rail_names == ["Zeno", "Ajax"]
    assert [row["rank"] for row in rail] == [1, 2]


async def test_rail_order_is_not_the_points_order(client, reset_db) -> None:
    """Guard the guard: this match really does separate the two orderings.

    Without it the test above could pass against a payload that still ranked by
    points, so pin that points-first would give the opposite list.
    """
    await _seed_wins_beat_points(reset_db)
    _html, data = await _page(client)

    last = data["turns"][-1]
    rail_order = [row["agent"] for row in last["standings"]["after"]]
    score_after = {a["agent"]: a["score_after"] for a in last["actions"]}
    points_order = sorted(rail_order, key=lambda seat: -score_after[seat])

    assert points_order == ["Ajax", "Zeno"]
    assert rail_order == ["Zeno", "Ajax"]


async def test_every_turn_carries_a_total_order(client, reset_db) -> None:
    """Each turn ships `before` and `after` snapshots covering every seat once.

    A total order is the whole point: with one, the viewer never has a tie left
    to break for itself.
    """
    await _seed_wins_beat_points(reset_db)
    _html, data = await _page(client)

    agents = set(data["agents"])
    for turn in data["turns"]:
        for which in ("before", "after"):
            rows = turn["standings"][which]
            assert len(rows) == len(agents)
            assert {row["agent"] for row in rows} == agents
            # Ranks run 1..N with no shared places, exactly as `rank_standings`
            # numbers the match pages — so list position IS the finishing order.
            assert [row["rank"] for row in rows] == list(range(1, len(agents) + 1))


async def test_round_win_credit_lands_on_the_turn_that_ends_the_round(
    client, reset_db
) -> None:
    """A round's win shows up in that turn's `after`, never in its `before`.

    This is what lets the animation credit the win once the round's last beat has
    played instead of jumping ahead of it.
    """
    await _seed_wins_beat_points(reset_db)
    _html, data = await _page(client)

    def wins(turn: dict[str, Any], which: str) -> dict[str, float]:
        return {row["agent"]: row["round_wins"] for row in turn["standings"][which]}

    first = data["turns"][0]
    assert wins(first, "before") == {"Zeno": 0, "Ajax": 0}
    assert wins(first, "after") == {"Zeno": 1.0, "Ajax": 0}
    assert wins(data["turns"][1], "before") == {"Zeno": 1.0, "Ajax": 0}
    # The last round's credit lands on the last turn's `after` — the server's own
    # tally, so it also matches the final scoreboard.
    assert wins(data["turns"][-1], "before") == {"Zeno": 2.0, "Ajax": 0}
    assert wins(data["turns"][-1], "after") == {"Zeno": 2.0, "Ajax": 1.0}


async def test_a_tied_round_ships_its_split_win(client, reset_db) -> None:
    """A tie splits the round, and the fraction survives into the payload."""
    await _seed_match(
        reset_db,
        rounds=[
            # Round 1: both HOARD to 2 — a dead-level round, split 0.5/0.5.
            [("Zeno", "HOARD", None, 2), ("Ajax", "HOARD", None, 2)],
            # Round 2: Zeno takes it outright.
            [("Zeno", "HOARD", None, 6), ("Ajax", "HELP", "Zeno", 0)],
        ],
        totals={"Ajax": (0.5, 2, 0), "Zeno": (1.5, 8, 6)},
        winner_seat="Zeno",
    )
    _html, data = await _page(client)

    after_round_one = {
        row["agent"]: row["round_wins"] for row in data["turns"][0]["standings"]["after"]
    }
    assert after_round_one == {"Zeno": 0.5, "Ajax": 0.5}
    final = {
        row["agent"]: row["round_wins"]
        for row in data["turns"][-1]["standings"]["after"]
    }
    assert final == {"Zeno": 1.5, "Ajax": 0.5}


# --- Helper parity: one award rule, two callers -------------------------------


def test_round_award_reproduces_the_rule_it_was_extracted_from() -> None:
    """The pure helper must match the inline rule ``award_round_winners`` had.

    Permutation-exhaustive on the tie cases (the same shape as
    ``test_finish_order_key_matches_both_old_sorts``), because a set-based
    reimplementation can agree on who won and still disagree on the share.
    """
    cases: list[list[tuple[str, int]]] = [
        [("a", 10), ("b", 6), ("c", 4)],       # one clear winner
        [("a", 8), ("b", 8), ("c", 8)],        # three-way tie
        [("a", 5), ("b", 5), ("c", 1)],        # two-way tie
        [("a", 0), ("b", 0), ("c", 0)],        # an all-zero round still has winners
        [("a", 3)],                            # a single player takes the round
    ]
    for case in cases:
        for ordering in permutations(case):
            scores = dict(ordering)
            # The rule as `award_round_winners` used to spell it, inline.
            top = max(scores.values(), default=0)
            old_winners = [key for key, score in scores.items() if score == top]
            old_share = 1.0 / len(old_winners) if old_winners else 0

            winners, share = round_award(scores)
            assert sorted(winners) == sorted(old_winners), scores
            assert share == old_share, scores
            # A round is worth exactly one win, however many ways it splits.
            assert share * len(winners) == pytest.approx(1.0), scores

    # No players at all: no winners, no share, no ZeroDivisionError.
    assert round_award({}) == ([], 0.0)


async def test_replay_wins_match_what_the_resolver_awards(db) -> None:
    """Same round scores, two callers, one answer.

    Runs the live path (``award_round_winners`` against real rows) and the replay
    path (``stamp_turn_standings`` over the matching turns) on the same three
    rounds — including a tie — and asserts the round-win tallies agree.
    """
    match = Match(
        id="G_PARITY",
        name="Parity",
        state=GameState.ACTIVE,
        scheduled_start=datetime.now(timezone.utc),
        total_rounds=3,
        turns_per_round=1,
    )
    db.add(match)
    await db.flush()
    seats = ["A", "B", "C"]
    players = []
    for i, seat in enumerate(seats):
        user = User(google_sub=f"parity-{i}", email=f"parity-{i}@t.com")
        db.add(user)
        await db.flush()
        agent, _version = await make_agent(db, user, name=seat)
        player = Player(match_id="G_PARITY", user_id=user.id, agent_id=agent.id, seat_name=seat)
        db.add(player)
        await db.flush()
        players.append(player)
    await db.commit()

    per_round = [{"A": 9, "B": 4, "C": 4}, {"A": 5, "B": 5, "C": 1}, {"A": 0, "B": 0, "C": 0}]
    for round_no, scores in enumerate(per_round, start=1):
        for player in players:
            player.current_round_score = scores[player.seat_name]
        await db.commit()
        await award_round_winners(db, match, round_no)
    for player in players:
        await db.refresh(player)
    live = {p.seat_name: p.total_round_wins for p in players}

    turns = [
        {
            "round": round_no,
            "actions": [
                {"agent": seat, "score_after": score} for seat, score in scores.items()
            ],
        }
        for round_no, scores in enumerate(per_round, start=1)
    ]
    # No `final_round_wins`: derive every round, so this compares the award rule
    # itself rather than the scoreboard anchor.
    stamp_turn_standings(turns, seats)
    replayed = {
        row["agent"]: row["round_wins"] for row in turns[-1]["standings"]["after"]
    }

    assert replayed == live
    # Round 1 outright, round 2 split two ways, round 3 split three ways.
    assert live["A"] == pytest.approx(1 + 0.5 + 1 / 3)
    assert live["C"] == pytest.approx(1 / 3)
