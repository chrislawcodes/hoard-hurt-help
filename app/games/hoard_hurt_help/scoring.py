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
    hurt_blocks,
    hurt_take,
    DEFAULT_MISSED_MESSAGE,
    DEFAULT_MUTUAL_HELP_MODE,
    HELP_POINTS,
    hoard_share,
    HURT_POINTS,
    LEGACY_MUTUAL_HELP_MODE,
    SCORE_FLOOR,
    MutualHelpMode,
    mode_needs_history,
    mutual_help_value,
)
from app.models.match import Match
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
        for pair in mutual_help_pairs(subs):
            counts[pair] = counts.get(pair, 0) + 1
    return counts


def mutual_help_pairs(subs: Iterable[TurnSubmission]) -> set[frozenset[int]]:
    """The unordered pairs that mutually HELPed each other within ONE turn.

    A pair appears at most once however the submissions are ordered, mirroring
    `resolve_turn`'s same-turn guard. Only reciprocal HELP counts —
    HOARD/HURT/defaulted rows contribute nothing.
    """
    help_targets = {s.player_id: s.target_player_id for s in subs if s.action == "HELP"}
    pairs: set[frozenset[int]] = set()
    for a, b in help_targets.items():
        if b is not None and help_targets.get(b) == a:
            pairs.add(frozenset({a, b}))
    return pairs


async def current_pact_values(
    db: AsyncSession,
    match_id: str,
    player_id: int,
    other_player_ids: Iterable[int],
    *,
    mode: MutualHelpMode | str = DEFAULT_MUTUAL_HELP_MODE,
) -> dict[int, int]:
    """Current mutual-help pact value between `player_id` and each other player.

    The value is what a mutual HELP between that pair would pay EACH side right
    now under this match's mode — always via `mutual_help_value`, so this preview
    and what `resolve_turn` actually pays cannot drift apart.

    For the modes whose payout depends on history (decay, once), `k` — the pair's
    match-wide count of prior mutual helps — comes from `mutual_help_counts`, the
    single source of that count; this function does not re-derive it any other
    way. A pair with no prior mutual help this match is absent from the counts map
    and so reads k=0. Only resolved turns are read, so — like `resolve_turn` —
    this is resume-safe: it has no in-memory-only state.

    The flat modes ignore history entirely, so they skip the scan rather than
    reading turns they would discard.
    """
    if not mode_needs_history(mode):
        flat = mutual_help_value(mode, 0)
        return {other_id: flat for other_id in other_player_ids}

    subs: list[TurnSubmission] = list(
        (
            await db.execute(
                select(TurnSubmission)
                .join(Turn, Turn.id == TurnSubmission.turn_id)
                .where(Turn.match_id == match_id, Turn.resolved_at.is_not(None))
                .order_by(TurnSubmission.turn_id)
            )
        )
        .scalars()
        .all()
    )
    by_turn: dict[int, list[TurnSubmission]] = {}
    for s in subs:
        by_turn.setdefault(s.turn_id, []).append(s)
    counts = mutual_help_counts(by_turn.values())
    # Same "previous turn" the resolver uses, so the preview and the payout agree.
    last_pairs = mutual_help_pairs(by_turn[max(by_turn)]) if by_turn else set()
    return {
        other_id: mutual_help_value(
            mode,
            counts.get(frozenset({player_id, other_id}), 0),
            repeated_last_turn=frozenset({player_id, other_id}) in last_pairs,
        )
        for other_id in other_player_ids
    }


async def resolve_turn(db: AsyncSession, turn: Turn) -> None:
    """Resolve one turn: materialize submissions, apply payoffs, persist deltas.

    Order matters and matches spec.md §5:
      1. Default any missing submission to HOARD (was_defaulted=True).
      2. Compute raw deltas (Hoard +2, Help +4 to target, Hurt -4 to target).
      3. Add the mutual-help bonus for any A↔B pair, at this match's mode rate —
         `mutual_help_value(mode, k)`, where k is how many times that same pair
         already mutually helped this match, from prior resolved turns.
      4. Apply the score floor at 0 to the FINAL per-player delta, not per-hurt.
      5. Persist post-floor `points_delta` and `round_score_after`.
      6. Mark turn resolved.
    """
    # This match's mutual-help mode. A NULL column is a row from before the mode
    # switch existed; every such match ran decay, so that — not today's shipped
    # default — is the correct reading of a missing value.
    match = (
        await db.execute(select(Match).where(Match.id == turn.match_id))
    ).scalar_one()
    mode = MutualHelpMode(match.mutual_help_mode or LEGACY_MUTUAL_HELP_MODE)

    # Players in this game.
    players: list[Player] = list(
        (await db.execute(select(Player).where(Player.match_id == turn.match_id)))
        .scalars()
        .all()
    )

    # Per-pair mutual-help decay: count how many times each pair already mutually
    # helped in this match's PRIOR resolved turns (the current turn isn't resolved
    # yet, and is excluded by id). Derived from history so it survives a DB resume.
    # Only needed for the modes whose payout depends on it; the flat modes never
    # read k, so they skip the scan instead of loading history they'd discard.
    prior_counts: dict[frozenset[int], int] = {}
    last_turn_pairs: set[frozenset[int]] = set()
    if mode_needs_history(mode):
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
        # NO_REPEATS only cares about the turn immediately before this one — the
        # highest prior resolved turn id. Turn ids increase across the whole match,
        # so "the previous turn" carries over a round boundary: the last turn of a
        # round and the first of the next are still back-to-back.
        if prior_by_turn:
            last_turn_pairs = mutual_help_pairs(prior_by_turn[max(prior_by_turn)])

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

    # HOARD is a contested pot: everyone who hoards this turn splits it, so the
    # per-hoarder payout can only be known once the whole turn is in. Count first,
    # then pay — a defaulted submission is a HOARD, so it takes a share and thins
    # everyone else's, exactly as a deliberate hoard would.
    hoard_each = hoard_share(sum(1 for s in submissions if s.action == "HOARD"))

    # What each player did this turn, so a HURT can be priced off its TARGET's
    # move. Built before paying anything: the take depends on the whole turn, the
    # same way the pot does.
    action_of = {s.player_id: s.action for s in submissions}
    target_of = {s.player_id: s.target_player_id for s in submissions}

    # A HURT whose target is HURTing the attacker back BLOCKS: both swings miss,
    # so neither lands damage and neither takes a payout. Resolved before the
    # attacker counts below, so a blocked attacker never thins a real one's share.
    blocked = {
        s.player_id
        for s in submissions
        if s.action == "HURT"
        and s.target_player_id is not None
        and hurt_blocks(
            action_of.get(s.target_player_id),
            target_of.get(s.target_player_id),
            s.player_id,
        )
    }

    # Attackers landing on each target, so they split the take — mobbing one
    # player is allowed, it just pays each attacker less (see `hurt_take`).
    #
    # A BETRAYER is excluded from this count. Their bonus is earned from a
    # relationship rather than grabbed off the table, so it is neither split nor
    # allowed to thin what the other attackers share. Counting them would let a
    # betrayal quietly halve a bystander's take on the same victim.
    attackers_on: dict[int, int] = {}
    for s in submissions:
        if (
            s.action == "HURT"
            and s.player_id not in blocked
            and s.target_player_id in delta
            and help_targets.get(s.target_player_id) != s.player_id
        ):
            attackers_on[s.target_player_id] = attackers_on.get(s.target_player_id, 0) + 1

    for s in submissions:
        if s.action == "HOARD":
            delta[s.player_id] += hoard_each
        elif s.action == "HELP" and s.target_player_id in delta:
            delta[s.target_player_id] += HELP_POINTS
        elif s.action == "HURT" and s.target_player_id in delta:
            if s.player_id in blocked:
                continue  # both swung at each other; nothing lands either way
            # The victim always takes the normal HURT_POINTS.
            delta[s.target_player_id] -= HURT_POINTS
            # What the ATTACKER takes depends on what the target was doing — a
            # betrayal pays most, a hoarder least, someone else's attacker
            # nothing. `hurt_take` is the single source; on a betrayal it returns
            # the BONUS only, because the target's HELP is credited above.
            delta[s.player_id] += hurt_take(
                action_of.get(s.target_player_id),
                target_helps_attacker=help_targets.get(s.target_player_id) == s.player_id,
                attackers_on_target=attackers_on.get(s.target_player_id, 1),
            )

    # Mutual-help bonus, added to each side once per A↔B pair. `mutual_help_value`
    # returns the pair's per-side TOTAL for this mode and repeat count; the base
    # HELP_POINTS is already in `delta` from the raw payoffs above, so only the
    # remainder is added here. Deriving the bonus from the total (rather than
    # computing it separately) is what keeps the resolver and the pre-move preview
    # from ever disagreeing.
    seen_pairs: set[frozenset[int]] = set()
    for a, b in help_targets.items():
        if b is None:
            continue
        if help_targets.get(b) == a:
            pair = frozenset({a, b})
            if pair not in seen_pairs:
                total = mutual_help_value(
                    mode,
                    prior_counts.get(pair, 0),
                    repeated_last_turn=pair in last_turn_pairs,
                )
                bonus = total - HELP_POINTS
                delta[a] += bonus
                delta[b] += bonus
                seen_pairs.add(pair)

    # Apply floor on final delta and persist.
    sub_by_player: dict[int, TurnSubmission] = {s.player_id: s for s in submissions}
    for p in players:
        new_score = p.current_round_score + delta[p.id]
        if new_score < SCORE_FLOOR:
            new_score = SCORE_FLOOR
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

    This is the *viewer's* running-score view — used for lead tracking. It floors
    each HURT individually and credits a mutual-help actor the decayed per-side
    total (`mutual_value` on the action, falling back to the fresh-pact
    HELP_POINTS + MUTUAL_HELP_BONUS if absent). When a player HURTs someone who is
    HELPing them this same turn (betraying a helper), the victim takes the normal
    HURT_POINTS and the ATTACKER gains a BETRAYAL_BONUS on top of the +HELP_POINTS
    they receive — mirroring `resolve_turn`. It is a display approximation and is
    deliberately distinct from `resolve_turn`, which is authoritative and floors
    the summed per-player delta. Keep them separate; do not route resolution
    through this helper.

    Action dicts use keys: "action", "agent_id", optional "target_id",
    optional "mutual", optional "mutual_value" (the decayed per-side total — the
    caller computes the per-pair decay; this helper has no match history).
    """
    new_inround = dict(inround)
    mutual_help = mutual_help_value(MutualHelpMode.FLAT_8, 0)
    # Same contested-pot rule as `resolve_turn`: count the hoarders before paying
    # any of them, or the mirror would credit a solo rate to a crowded pot.
    actions = list(actions)
    hoard_each = hoard_share(sum(1 for a in actions if a["action"] == "HOARD"))
    # Who each HELPer targeted — to detect a betrayal HURT (HURTing a same-turn helper).
    help_targets = {
        a["agent_id"]: a.get("target_id") for a in actions if a["action"] == "HELP"
    }
    # What everyone did, so a HURT can be priced off its TARGET's move, and how
    # many non-betraying attackers share each target — both mirroring
    # `resolve_turn` exactly. A betrayer neither splits nor thins the pool.
    action_by = {a["agent_id"]: a["action"] for a in actions}
    target_by = {a["agent_id"]: a.get("target_id") for a in actions}
    attackers_on: dict[Any, int] = {}
    for a in actions:
        tgt = a.get("target_id")
        if (
            a["action"] == "HURT"
            and tgt
            and not hurt_blocks(action_by.get(tgt), target_by.get(tgt), a["agent_id"])
            and help_targets.get(tgt) != a["agent_id"]
        ):
            attackers_on[tgt] = attackers_on.get(tgt, 0) + 1

    for a in actions:
        action = a["action"]
        actor = a["agent_id"]
        target = a.get("target_id")
        mutual = a.get("mutual", False)
        if action == "HOARD":
            new_inround[actor] = new_inround.get(actor, 0) + hoard_each
        elif action == "HELP" and mutual:
            new_inround[actor] = new_inround.get(actor, 0) + a.get("mutual_value", mutual_help)
        elif action == "HELP" and target:
            new_inround[target] = new_inround.get(target, 0) + HELP_POINTS
        elif action == "HURT" and target:
            # A blocked pair (both HURTing each other) misses entirely: no damage
            # and no take, mirroring `resolve_turn`.
            if hurt_blocks(action_by.get(target), target_by.get(target), actor):
                continue
            # The victim always takes the normal HURT_POINTS (floored per-hurt).
            new_inround[target] = max(
                SCORE_FLOOR, new_inround.get(target, 0) - HURT_POINTS
            )
            # The attacker's take is priced off what the TARGET did, via the same
            # `hurt_take` the resolver uses — this mirror is display-only, so it
            # must never invent a rate of its own.
            new_inround[actor] = new_inround.get(actor, 0) + hurt_take(
                action_by.get(target),
                target_helps_attacker=help_targets.get(target) == actor,
                attackers_on_target=attackers_on.get(target, 1),
            )
    return new_inround
