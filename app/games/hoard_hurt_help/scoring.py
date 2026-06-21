"""Prisoner's Dilemma turn scoring — HOARD/HELP/HURT payoffs.

The PD-specific per-turn math (raw deltas, mutual-help bonus, score floor).
Relocated verbatim from app/engine/resolver.py; the math is unchanged.
Read it with spec.md §5 alongside.
"""

from datetime import datetime, timezone

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.games.hoard_hurt_help.rules import (
    DEFAULT_MISSED_MESSAGE,
    HELP_POINTS,
    HOARD_POINTS,
    HURT_POINTS,
    MUTUAL_HELP_BONUS,
)
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission


async def resolve_turn(db: AsyncSession, turn: Turn) -> None:
    """Resolve one turn: materialize submissions, apply payoffs, persist deltas.

    Order matters and matches spec.md §5:
      1. Default any missing submission to HOARD (was_defaulted=True).
      2. Compute raw deltas (Hoard +2, Help +4 to target, Hurt -4 to target).
      3. Add the mutual-help bonus (+4 each side) for any A↔B pair.
      4. Apply the score floor at 0 to the FINAL per-player delta, not per-hurt.
      5. Persist post-floor `points_delta` and `round_score_after`.
      6. Mark turn resolved.
    """
    # Players in this game.
    players: list[Player] = list(
        (await db.execute(select(Player).where(Player.match_id == turn.match_id)))
        .scalars()
        .all()
    )

    # Materialize submissions, defaulting missing ones to HOARD.
    submissions: list[TurnSubmission] = list(
        (await db.execute(select(TurnSubmission).where(TurnSubmission.turn_id == turn.id)))
        .scalars()
        .all()
    )
    submitted_player_ids = {s.player_id for s in submissions}
    for p in players:
        if p.id not in submitted_player_ids:
            default = TurnSubmission(
                turn_id=turn.id,
                player_id=p.id,
                action="HOARD",
                target_player_id=None,
                message=DEFAULT_MISSED_MESSAGE,
                was_defaulted=True,
                submitted_at=None,
            )
            db.add(default)
            submissions.append(default)
    await db.flush()

    # Raw deltas (pre-floor).
    delta: dict[int, int] = {p.id: 0 for p in players}

    for s in submissions:
        if s.action == "HOARD":
            delta[s.player_id] += HOARD_POINTS
        elif s.action == "HELP" and s.target_player_id in delta:
            delta[s.target_player_id] += HELP_POINTS
        elif s.action == "HURT" and s.target_player_id in delta:
            delta[s.target_player_id] -= HURT_POINTS

    # Mutual-help bonus: for each HELP pair where both helped each other,
    # add +4 to each side, but only once per pair.
    help_targets = {
        s.player_id: s.target_player_id for s in submissions if s.action == "HELP"
    }
    seen_pairs: set[frozenset[int]] = set()
    for a, b in help_targets.items():
        if b is None:
            continue
        if help_targets.get(b) == a:
            pair = frozenset({a, b})
            if pair not in seen_pairs:
                delta[a] += MUTUAL_HELP_BONUS
                delta[b] += MUTUAL_HELP_BONUS
                seen_pairs.add(pair)

    # Apply floor on final delta and persist.
    sub_by_player: dict[int, TurnSubmission] = {s.player_id: s for s in submissions}
    for p in players:
        new_score = p.current_round_score + delta[p.id]
        if new_score < 0:
            new_score = 0
        actual_delta = new_score - p.current_round_score
        p.current_round_score = new_score
        s = sub_by_player[p.id]
        s.points_delta = actual_delta
        s.round_score_after = new_score

    turn.resolved_at = datetime.now(timezone.utc)
    await db.commit()


def apply_inround_turn(
    inround: Mapping[str, int], actions: Iterable[Mapping[str, Any]]
) -> dict[str, int]:
    """Return a new in-round score map after applying one turn's actions.

    This is the *viewer's* running-score view — used for lead tracking and the
    win-probability features. It floors each HURT individually and credits a
    mutual-help actor the full net (HELP_POINTS + MUTUAL_HELP_BONUS). It is a
    display approximation and is deliberately distinct from `resolve_turn`,
    which is authoritative and floors the summed per-player delta. Keep them
    separate; do not route resolution through this helper.

    Action dicts use keys: "action", "agent_id", optional "target_id",
    optional "mutual".
    """
    new_inround = dict(inround)
    mutual_help = HELP_POINTS + MUTUAL_HELP_BONUS
    for a in actions:
        action = a["action"]
        actor = a["agent_id"]
        target = a.get("target_id")
        mutual = a.get("mutual", False)
        if action == "HOARD":
            new_inround[actor] = new_inround.get(actor, 0) + HOARD_POINTS
        elif action == "HELP" and mutual:
            new_inround[actor] = new_inround.get(actor, 0) + mutual_help
        elif action == "HELP" and target:
            new_inround[target] = new_inround.get(target, 0) + HELP_POINTS
        elif action == "HURT" and target:
            new_inround[target] = max(0, new_inround.get(target, 0) - HURT_POINTS)
    return new_inround
