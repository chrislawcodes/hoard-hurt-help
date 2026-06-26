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
    BETRAYAL_HURT_POINTS,
    DEFAULT_MISSED_MESSAGE,
    HELP_POINTS,
    HOARD_POINTS,
    HURT_POINTS,
    MUTUAL_HELP_BONUS,
    MUTUAL_HELP_FLOOR,
)
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission


def mutual_help_counts(
    prior_turns: Iterable[Iterable[TurnSubmission]],
) -> dict[frozenset[int], int]:
    """How many prior turns each unordered pair mutually HELPed each other.

    `prior_turns` is one iterable of submissions per *resolved* turn. A pair is
    counted at most once per turn (mirroring `resolve_turn`'s same-turn guard).
    Only reciprocal HELP pairs count — HOARD/HURT/defaulted rows contribute 0.
    This is the single source of the decay counter `k`; reuse it, don't re-scan.
    """
    counts: dict[frozenset[int], int] = {}
    for subs in prior_turns:
        help_targets = {s.player_id: s.target_player_id for s in subs if s.action == "HELP"}
        seen: set[frozenset[int]] = set()
        for a, b in help_targets.items():
            if b is None or help_targets.get(b) != a:
                continue
            pair = frozenset({a, b})
            if pair not in seen:
                seen.add(pair)
                counts[pair] = counts.get(pair, 0) + 1
    return counts


async def resolve_turn(db: AsyncSession, turn: Turn) -> None:
    """Resolve one turn: materialize submissions, apply payoffs, persist deltas.

    Order matters and matches spec.md §5:
      1. Default any missing submission to HOARD (was_defaulted=True).
      2. Compute raw deltas (Hoard +2, Help +4 to target, Hurt -4 to target).
      3. Add the mutual-help bonus for any A↔B pair, DECAYED by how many times that
         same pair already mutually helped this match (max(2, 8-k) per side; floor
         at the Hoard value). k is derived from prior resolved turns.
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

    # Per-pair mutual-help decay: count how many times each pair already mutually
    # helped in this match's PRIOR resolved turns (the current turn isn't resolved
    # yet, and is excluded by id). Derived from history so it survives a DB resume.
    prior_subs: list[TurnSubmission] = list(
        (
            await db.execute(
                select(TurnSubmission)
                .join(Turn, Turn.id == TurnSubmission.turn_id)
                .where(
                    Turn.match_id == turn.match_id,
                    Turn.resolved_at.is_not(None),
                    Turn.id != turn.id,
                )
                .order_by(TurnSubmission.turn_id)
            )
        )
        .scalars()
        .all()
    )
    prior_by_turn: dict[int, list[TurnSubmission]] = {}
    for s in prior_subs:
        prior_by_turn.setdefault(s.turn_id, []).append(s)
    prior_counts = mutual_help_counts(prior_by_turn.values())

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

    # Who each HELPer targeted — needed both for the mutual-help bonus below and
    # to detect a betrayal HURT (HURTing someone who is HELPing you this turn).
    help_targets = {
        s.player_id: s.target_player_id for s in submissions if s.action == "HELP"
    }

    for s in submissions:
        if s.action == "HOARD":
            delta[s.player_id] += HOARD_POINTS
        elif s.action == "HELP" and s.target_player_id in delta:
            delta[s.target_player_id] += HELP_POINTS
        elif s.action == "HURT" and s.target_player_id in delta:
            # Betraying a helper: HURTing a player who is HELPing you this same
            # turn lands for BETRAYAL_HURT_POINTS instead of the base HURT_POINTS.
            betrayed_helper = help_targets.get(s.target_player_id) == s.player_id
            delta[s.target_player_id] -= (
                BETRAYAL_HURT_POINTS if betrayed_helper else HURT_POINTS
            )

    # Mutual-help bonus, DECAYED per pair: for each HELP pair where both helped
    # each other, add the bonus to each side once. The bonus shrinks by 1 for each
    # prior mutual help by this same pair (k), flooring the pair's per-side total at
    # MUTUAL_HELP_FLOOR: total = base HELP_POINTS + bonus = max(MUTUAL_HELP_FLOOR, 8-k).
    seen_pairs: set[frozenset[int]] = set()
    for a, b in help_targets.items():
        if b is None:
            continue
        if help_targets.get(b) == a:
            pair = frozenset({a, b})
            if pair not in seen_pairs:
                k = prior_counts.get(pair, 0)
                bonus = max(MUTUAL_HELP_FLOOR - HELP_POINTS, MUTUAL_HELP_BONUS - k)
                delta[a] += bonus
                delta[b] += bonus
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
    mutual-help actor the full net (HELP_POINTS + MUTUAL_HELP_BONUS). A HURT
    against a player who HELPs the attacker this same turn lands for
    BETRAYAL_HURT_POINTS, mirroring `resolve_turn`. It is a display approximation
    and is deliberately distinct from `resolve_turn`, which is authoritative and
    floors the summed per-player delta. Keep them separate; do not route
    resolution through this helper.

    Action dicts use keys: "action", "agent_id", optional "target_id",
    optional "mutual".
    """
    new_inround = dict(inround)
    mutual_help = HELP_POINTS + MUTUAL_HELP_BONUS
    # Who each HELPer targeted — to detect a betrayal HURT (HURTing a same-turn helper).
    help_targets = {
        a["agent_id"]: a.get("target_id") for a in actions if a["action"] == "HELP"
    }
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
            damage = (
                BETRAYAL_HURT_POINTS if help_targets.get(target) == actor else HURT_POINTS
            )
            new_inround[target] = max(0, new_inround.get(target, 0) - damage)
    return new_inround
