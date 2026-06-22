"""PD win-probability adapter for the replay viewer.

The replay attaches a per-turn win-probability band (p/lo/hi) to each agent so
the standings rail can show "who's likely to win this round". This module is the
bridge between the viewer's per-turn history and the engine's win-probability
model (`app/engine/win_probability.py`): it converts the viewer history into the
engine's `ActionRecord`/`PlayerRecord` shapes, runs `score_round_win()` at each
turn boundary, and wraps the result in calibration bands.

Split out of `viewer.py` verbatim so the payload builder there carries only the
rc_data/replay shaping. `viewer.py` imports `_compute_round_win_probs` from here.
"""

from __future__ import annotations

from typing import Any

from app.games.hoard_hurt_help.scoring import apply_inround_turn

# Calibration ±pp per round (measured on held-out test set via permutation
# of predicted vs actual within calibration buckets). Round 1 excluded —
# all ten players sit at ~10%, ranges add no signal.
_ROUND_CAL_MAE: dict[int, float] = {
    2: 0.06, 3: 0.06, 4: 0.05, 5: 0.08, 6: 0.07, 7: 0.05,
}


def _compute_round_win_probs(
    scoreboard: list[dict[str, Any]],
    history: list[dict[str, Any]],
    turns_per_round: int,
) -> dict[tuple[int, int], dict[str, dict[str, float]]]:
    """Return {(round, turn): {agent_id: {p, lo, hi}}} for every completed turn.

    Converts the viewer history into engine records, runs score_round_win() at
    each turn boundary, and attaches calibration bands.  Returns {} silently if
    the model file is absent.
    """
    from app.engine.game_records import ActionRecord, PlayerRecord
    from app.engine.win_probability import score_round_win

    agents = [r["agent_id"] for r in scoreboard]
    if not agents or not history:
        return {}

    # Last turn number per round — needed to detect round completion.
    last_turn_by_round: dict[int, int] = {}
    for h in history:
        rnd = h["round"]
        last_turn_by_round[rnd] = max(last_turn_by_round.get(rnd, 0), h["turn"])

    inround: dict[str, int] = {a: 0 for a in agents}
    round_wins: dict[str, float] = {a: 0.0 for a in agents}
    current_round: int | None = None
    all_action_records: list[ActionRecord] = []
    result: dict[tuple[int, int], dict[str, dict[str, float]]] = {}

    for h in history:
        rnd, turn = h["round"], h["turn"]

        if rnd != current_round:
            current_round = rnd
            inround = {a: 0 for a in agents}

        scores_before = dict(inround)

        # Apply this turn's actions to get post-turn scores.
        new_inround = apply_inround_turn(inround, h["actions"])

        for a in h["actions"]:
            actor = a["agent_id"]
            all_action_records.append(
                ActionRecord(
                    round=rnd,
                    turn=turn,
                    actor_id=actor,
                    action=a["action"],
                    target_id=a.get("target_id"),
                    message="",
                    points_delta=new_inround.get(actor, 0) - scores_before.get(actor, 0),
                    round_score_after=new_inround.get(actor, 0),
                    was_defaulted=a.get("was_defaulted", False),
                )
            )

        inround = new_inround

        player_records = [
            PlayerRecord(
                agent_id=a,
                round_score=inround.get(a, 0),
                total_score=0,
                round_wins=round_wins[a],
            )
            for a in agents
        ]

        probs = score_round_win(player_records, all_action_records, rnd, turn, turns_per_round)
        if probs:
            cal = _ROUND_CAL_MAE.get(rnd, 0.0)
            result[(rnd, turn)] = {
                pid: {
                    "p": round(p, 3),
                    "lo": round(max(0.0, p - cal), 3),
                    "hi": round(min(1.0, p + cal), 3),
                }
                for pid, p in probs.items()
            }

        # At end of round, credit round wins for the next round's PlayerRecords.
        if turn == last_turn_by_round.get(rnd, -1):
            best = max(inround.values()) if inround else 0
            winners = [a for a in agents if inround.get(a, 0) == best]
            share = 1.0 / len(winners) if winners else 0.0
            for w in winners:
                round_wins[w] += share

    return result
