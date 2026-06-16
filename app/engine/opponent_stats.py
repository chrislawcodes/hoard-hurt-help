"""Per-opponent, action-derived stats and the bounded short-list selection.

All facts here come from actions only (HOARD/HELP/HURT + target) — never from
message text (v1 stays cheap and objective; the message-reading tier is v2).
Everything is deterministic so summaries are stable and tests aren't flaky.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Literal

from app.engine.action_vocab import pd_action_names
from app.engine.game_records import ActionRecord, PlayerRecord
from app.schemas.agent import OpponentsAggregate, OpponentStat, StyleMix

OpponentReason = Literal["interacted", "threat", "neighbor", "flagged"]

# Tunable selection caps (module-level so large-N games can be tuned — US5).
MAX_SHORTLIST = 12
TOP_THREATS = 3
NEIGHBOR_RADIUS = 2

def _style_index() -> dict[str, int]:
    """Map each action name to its style-bucket position (HOARD=0, HELP=1, HURT=2).

    The game's ordered action names drive the per-action style buckets; the
    StyleMix fields and the counts[0..2] reads assume this order."""
    return {name: i for i, name in enumerate(pd_action_names())}


def rank_players(players: Sequence[PlayerRecord]) -> list[PlayerRecord]:
    """Players by round_score desc, then agent_id asc (deterministic tiebreak)."""
    return sorted(players, key=lambda p: (-p.round_score, p.agent_id))


def ranks_by_agent(players: Sequence[PlayerRecord]) -> dict[str, int]:
    """1-based rank per agent_id."""
    return {p.agent_id: i + 1 for i, p in enumerate(rank_players(players))}


def _ordered_turn_keys(actions: Sequence[ActionRecord]) -> list[tuple[int, int]]:
    return sorted({(a.round, a.turn) for a in actions})


def _style_mix(counts: list[int]) -> StyleMix:
    total = counts[0] + counts[1] + counts[2]
    if total == 0:
        return StyleMix(hoard_pct=0, help_pct=0, hurt_pct=0)
    return StyleMix(
        hoard_pct=round(100 * counts[0] / total),
        help_pct=round(100 * counts[1] / total),
        hurt_pct=round(100 * counts[2] / total),
    )


def build_opponent_view(
    you: str,
    players: Sequence[PlayerRecord],
    actions: Sequence[ActionRecord],
    flagged_ids: set[str],
) -> tuple[list[OpponentStat], OpponentsAggregate | None]:
    """Return the capped opponent short-list + an aggregate for everyone else.

    `flagged_ids` are opponents the caller wants forced onto the list (e.g. they
    addressed you, or broke their pattern). Selection priority, then capped at
    MAX_SHORTLIST: interacted-last-turn > top threats > score-neighbors > flagged.
    """
    opponents = [p for p in players if p.agent_id != you]
    if not opponents:
        return [], None

    ranked = rank_players(players)
    rank_of = ranks_by_agent(players)
    your_rank = rank_of.get(you, len(ranked))
    opponent_ids = {p.agent_id for p in opponents}

    # Toward-you tallies.
    style_index = _style_index()
    _, help_action, hurt_action = pd_action_names()
    helped_you: Counter[str] = Counter()
    hurt_you: Counter[str] = Counter()
    style_counts: dict[str, list[int]] = {}
    for a in actions:
        idx = style_index.get(a.action)
        if idx is not None:
            style_counts.setdefault(a.actor_id, [0, 0, 0])[idx] += 1
        if a.actor_id != you and a.target_id == you:
            if a.action == help_action:
                helped_you[a.actor_id] += 1
            elif a.action == hurt_action:
                hurt_you[a.actor_id] += 1

    turn_keys = _ordered_turn_keys(actions)
    last_rt = turn_keys[-1] if turn_keys else None

    # Who interacted with you on the last resolved turn (either direction).
    interacted: set[str] = set()
    if last_rt is not None:
        for a in actions:
            if (a.round, a.turn) != last_rt:
                continue
            tid = a.target_id
            if a.actor_id == you and tid is not None and tid in opponent_ids:
                interacted.add(tid)
            if tid == you and a.actor_id in opponent_ids:
                interacted.add(a.actor_id)

    # Selection with reason priority; dict preserves first-seen reason/order.
    selected: dict[str, OpponentReason] = {}

    def add(agent_id: str, reason: OpponentReason) -> None:
        if agent_id != you and agent_id in opponent_ids and agent_id not in selected:
            selected[agent_id] = reason

    for agent_id in sorted(interacted):
        add(agent_id, "interacted")
    for p in [p for p in ranked if p.agent_id != you][:TOP_THREATS]:
        add(p.agent_id, "threat")
    for p in ranked:
        if p.agent_id == you:
            continue
        if abs(rank_of[p.agent_id] - your_rank) <= NEIGHBOR_RADIUS:
            add(p.agent_id, "neighbor")
    for agent_id in sorted(flagged_ids):
        add(agent_id, "flagged")

    short = list(selected.items())[:MAX_SHORTLIST]
    succ = {turn_keys[i]: turn_keys[i + 1] for i in range(len(turn_keys) - 1)}
    round_score_of = {p.agent_id: p.round_score for p in players}

    stats: list[OpponentStat] = []
    for agent_id, reason in short:
        returned_help, returned_hurt = _reciprocity(you, agent_id, actions, succ)
        # reason is one of the four selection labels by construction.
        stats.append(
            OpponentStat(
                agent_id=agent_id,
                round_score=round_score_of.get(agent_id, 0),
                helped_you=helped_you.get(agent_id, 0),
                hurt_you=hurt_you.get(agent_id, 0),
                returned_help=returned_help,
                returned_hurt=returned_hurt,
                style=_style_mix(style_counts.get(agent_id, [0, 0, 0])),
                reason=reason,
            )
        )

    aggregate = _aggregate(set(selected), opponents, actions, last_rt)
    return stats, aggregate


def _reciprocity(
    you: str,
    opp: str,
    actions: Sequence[ActionRecord],
    succ: dict[tuple[int, int], tuple[int, int]],
) -> tuple[bool, bool]:
    """Next-turn mirror: did `opp` mirror your move the very next resolved turn?"""
    _, help_action, hurt_action = pd_action_names()
    your_help = {(a.round, a.turn) for a in actions if a.actor_id == you and a.action == help_action and a.target_id == opp}
    your_hurt = {(a.round, a.turn) for a in actions if a.actor_id == you and a.action == hurt_action and a.target_id == opp}
    opp_help_you = {(a.round, a.turn) for a in actions if a.actor_id == opp and a.action == help_action and a.target_id == you}
    opp_hurt_you = {(a.round, a.turn) for a in actions if a.actor_id == opp and a.action == hurt_action and a.target_id == you}
    returned_help = any(succ.get(k) in opp_help_you for k in your_help)
    returned_hurt = any(succ.get(k) in opp_hurt_you for k in your_hurt)
    return returned_help, returned_hurt


def _aggregate(
    selected_ids: set[str],
    opponents: Sequence[PlayerRecord],
    actions: Sequence[ActionRecord],
    last_rt: tuple[int, int] | None,
) -> OpponentsAggregate | None:
    folded = [o.agent_id for o in opponents if o.agent_id not in selected_ids]
    if not folded:
        return None
    folded_set = set(folded)
    hoard_action, help_action, hurt_action = pd_action_names()
    hoard = help = hurt = 0
    if last_rt is not None:
        for a in actions:
            if (a.round, a.turn) != last_rt or a.actor_id not in folded_set:
                continue
            if a.action == hoard_action:
                hoard += 1
            elif a.action == help_action:
                help += 1
            elif a.action == hurt_action:
                hurt += 1
    return OpponentsAggregate(count=len(folded), hoard=hoard, help=help, hurt=hurt)
