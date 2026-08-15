"""Per-turn standings for the replay rail — ranked here, drawn by the viewer.

The animated replay shows a standings rail beside the robot circle. It used to
work both of those numbers out in JavaScript, and both were its own invention:

- It ranked the rail by in-round points first, round-wins second. Every ranking
  on the server puts round-wins first (``rank_standings``,
  ``finish_order_sort_key``). A finished match renders the final scoreboard and
  the rail on the same page, so the two lists openly disagreed: a seat with 2
  round-wins and 3 points led one list and sat below a 0-win, 9-point seat in
  the other.
- It re-derived the round-win award (top scorer takes the round, a tie splits it
  evenly) — a second copy of the rule in ``award_round_winners``.

So the server ships the answer instead. Every turn in the replay payload carries
the standings BEFORE it plays and AFTER it resolves, already ordered and already
carrying each seat's running round-wins tally. The ranking comes from
``rank_standings`` (the one the match pages use) and the award from
``round_award`` (the one live play uses). The JS holds neither rule.

This is the same lesson the ``score_after`` field learned in #645: ship the
answer, never the rule.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.engine.resolver import round_award
from app.read_models.matches import rank_standings


def _snapshot(
    agents: Sequence[str],
    round_score: Mapping[str, int],
    round_wins: Mapping[str, float],
) -> list[dict[str, Any]]:
    """Rank one moment's standings into the ordered list the rail draws.

    Builds one ``rank_standings`` row per seat, in the payload's own seat order,
    and hands them to the server's ranking helper. The result is a total order —
    position 0 is first place, and each entry carries the server's own rank
    number — so the viewer never breaks a tie itself. The sort is stable, so
    seats level on both wins and points keep the payload's seat order, which is
    the ranked scoreboard order the match pages already show.

    A seat with nothing recorded yet reads as zero: the rail lists every seat
    from the first turn, so an absent seat means "no points / no wins", not
    missing data.
    """
    return [
        {
            "agent": row["agent_id"],
            "rank": row["rank"],
            "round_wins": row["round_wins"],
        }
        for row in rank_standings(
            {
                "agent_id": agent,
                "round_score": round_score.get(agent, 0),
                "round_wins": round_wins.get(agent, 0.0),
            }
            for agent in agents
        )
    ]


def stamp_turn_standings(
    turns: list[dict[str, Any]],
    agents: Sequence[str],
    *,
    final_round_wins: Mapping[str, float] | None = None,
) -> None:
    """Attach a ``standings`` block to every turn of a replay payload, in place.

    Each turn gains ``{"standings": {"before": [...], "after": [...]}}``, where a
    snapshot is the ordered rail: ``{agent, rank, round_wins}`` per seat, first
    place first. ``before`` is where things stand as the turn starts playing;
    ``after`` is where they stand once it has resolved — so a round-win credit
    lands on the turn that ENDS the round, never before its last beat has played.

    In-round points come from each action's ``score_after`` (the resolver's own
    post-floor running total) and reset when the round number changes, mirroring
    ``current_round_score``. Round wins come from :func:`round_award`, the rule
    live play is scored by.

    ``final_round_wins`` is the server's own round-win tally for this match
    (``Player.total_round_wins``, reaching here as the scoreboard's
    ``round_wins``). When given it becomes the last turn's ``after`` tally
    verbatim, and the trailing round is not awarded here — whether that round has
    been awarded is already answered by those numbers, so the rail finishes on
    the server's count rather than a guess about a round that may still be in
    play. Pass ``None`` only for a standalone recording with no live scoreboard
    behind it (the bundled sample replay); every round present is then awarded.
    """
    if not turns:
        return

    last_index_of_round: dict[int, int] = {}
    for i, turn in enumerate(turns):
        last_index_of_round[turn["round"]] = i
    last_turn_index = len(turns) - 1
    trailing_round = turns[-1]["round"]

    round_score: dict[str, int] = dict.fromkeys(agents, 0)
    round_wins: dict[str, float] = dict.fromkeys(agents, 0.0)
    current_round: int | None = None

    for i, turn in enumerate(turns):
        if turn["round"] != current_round:
            current_round = turn["round"]
            round_score = dict.fromkeys(agents, 0)

        before = _snapshot(agents, round_score, round_wins)

        for action in turn["actions"]:
            score_after = action.get("score_after")
            if isinstance(score_after, int) and action["agent"] in round_score:
                round_score[action["agent"]] = score_after

        # The turn that ends a round carries that round's win credit — except for
        # the trailing round when the server has already told us its tally.
        awards_here = last_index_of_round[turn["round"]] == i and not (
            turn["round"] == trailing_round and final_round_wins is not None
        )
        if awards_here:
            winners, share = round_award(round_score)
            for winner in winners:
                round_wins[winner] = round_wins.get(winner, 0.0) + share
        if i == last_turn_index and final_round_wins is not None:
            round_wins = dict(final_round_wins)

        turn["standings"] = {
            "before": before,
            "after": _snapshot(agents, round_score, round_wins),
        }
