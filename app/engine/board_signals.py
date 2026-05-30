"""Whole-board signals the server can see but a single bot can't cheaply compute.

Alliances (mutual-help clusters), the round's cooperation "temperature", who is
surging, and which opponents broke their established pattern. Action-derived and
deterministic — no message text (v1).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Literal

from app.engine.game_records import ActionRecord, PlayerRecord
from app.schemas.agent import Alliance, BoardSignals

TemperatureLabel = Literal["hostile", "mixed", "cooperative"]

# Tunable thresholds (module-level for tuning at scale).
ALLY_MIN_HELPS = 2
MAX_ALLIANCES = 5
SURGE_RANK_JUMP = 2
SURGE_WINDOW = 3
MAX_SURGING = 2
DOMINANT_STYLE_SHARE = 0.6
MIN_PRIOR_ACTIONS = 2

_HOSTILE_BELOW = 0.33
_COOPERATIVE_ABOVE = 0.66


def compute_board_signals(
    players: Sequence[PlayerRecord],
    actions: Sequence[ActionRecord],
    current_round: int,
) -> BoardSignals:
    """Assemble the whole-board signals for the current round."""
    round_actions = [a for a in actions if a.round == current_round]
    temp, label = _temperature(round_actions)
    return BoardSignals(
        alliances=detect_alliances(round_actions),
        cooperation_temperature=temp,
        temperature_label=label,
        surging=detect_surging(players, round_actions),
    )


def _temperature(round_actions: Sequence[ActionRecord]) -> tuple[float, TemperatureLabel]:
    helps = sum(1 for a in round_actions if a.action == "HELP")
    hurts = sum(1 for a in round_actions if a.action == "HURT")
    if helps + hurts == 0:
        return 0.5, "mixed"
    temp = helps / (helps + hurts)
    if temp < _HOSTILE_BELOW:
        return temp, "hostile"
    if temp > _COOPERATIVE_ABOVE:
        return temp, "cooperative"
    return temp, "mixed"


def _help_edges(round_actions: Sequence[ActionRecord]) -> dict[tuple[str, str], int]:
    """Directed HELP edge weights A->B over the given actions."""
    edges: Counter[tuple[str, str]] = Counter()
    for a in round_actions:
        if a.action == "HELP" and a.target_id is not None:
            edges[(a.actor_id, a.target_id)] += 1
    return dict(edges)


def detect_alliances(round_actions: Sequence[ActionRecord]) -> list[Alliance]:
    """Connected components of the mutual-help graph, strongest first."""
    edges = _help_edges(round_actions)
    adjacency: dict[str, set[str]] = defaultdict(set)
    mutual_strength: dict[frozenset[str], int] = {}
    seen_pairs: set[frozenset[str]] = set()
    for (a, b), w in edges.items():
        pair = frozenset((a, b))
        if pair in seen_pairs or a == b:
            continue
        back = edges.get((b, a), 0)
        if w >= ALLY_MIN_HELPS and back >= ALLY_MIN_HELPS:
            seen_pairs.add(pair)
            adjacency[a].add(b)
            adjacency[b].add(a)
            mutual_strength[pair] = w + back

    # Connected components over mutual edges.
    components: list[set[str]] = []
    visited: set[str] = set()
    for node in adjacency:
        if node in visited:
            continue
        stack = [node]
        comp: set[str] = set()
        while stack:
            n = stack.pop()
            if n in visited:
                continue
            visited.add(n)
            comp.add(n)
            stack.extend(adjacency[n] - visited)
        components.append(comp)

    alliances: list[Alliance] = []
    for comp in components:
        strength = sum(
            s for pair, s in mutual_strength.items() if pair <= comp
        )
        alliances.append(Alliance(members=sorted(comp), strength=strength))
    alliances.sort(key=lambda al: (-al.strength, al.members))
    return alliances[:MAX_ALLIANCES]


def detect_surging(
    players: Sequence[PlayerRecord],
    round_actions: Sequence[ActionRecord],
) -> list[str]:
    """Agents whose rank improved by >= SURGE_RANK_JUMP over the last window."""
    turns = sorted({a.turn for a in round_actions})
    if len(turns) < 2:
        return []
    now_turn = turns[-1]
    past_turn = turns[max(0, len(turns) - 1 - SURGE_WINDOW)]
    now_rank = _ranks_at_turn(players, round_actions, now_turn)
    past_rank = _ranks_at_turn(players, round_actions, past_turn)

    improvements: list[tuple[int, str]] = []
    for agent_id, now_r in now_rank.items():
        past_r = past_rank.get(agent_id)
        if past_r is None:
            continue
        jump = past_r - now_r  # positive == climbed toward rank 1
        if jump >= SURGE_RANK_JUMP:
            improvements.append((jump, agent_id))
    improvements.sort(key=lambda t: (-t[0], t[1]))
    return [agent_id for _, agent_id in improvements[:MAX_SURGING]]


def _ranks_at_turn(
    players: Sequence[PlayerRecord],
    round_actions: Sequence[ActionRecord],
    turn: int,
) -> dict[str, int]:
    """Reconstruct 1-based ranks using each player's round_score_after at <= turn."""
    score: dict[str, int] = {p.agent_id: 0 for p in players}
    latest_turn_seen: dict[str, int] = {}
    for a in round_actions:
        if a.turn <= turn and a.turn >= latest_turn_seen.get(a.actor_id, -1):
            score[a.actor_id] = a.round_score_after
            latest_turn_seen[a.actor_id] = a.turn
    ordered = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    return {agent_id: i + 1 for i, (agent_id, _) in enumerate(ordered)}


def detect_pattern_breaks(actions: Sequence[ActionRecord]) -> list[str]:
    """Actors whose last-turn action broke a previously dominant style."""
    keys = sorted({(a.round, a.turn) for a in actions})
    if len(keys) < 2:
        return []
    last_rt = keys[-1]
    prior_styles: dict[str, Counter[str]] = defaultdict(Counter)
    last_action: dict[str, str] = {}
    for a in actions:
        if (a.round, a.turn) == last_rt:
            last_action[a.actor_id] = a.action
        else:
            prior_styles[a.actor_id][a.action] += 1

    broken: list[str] = []
    for actor_id, this_action in last_action.items():
        counts = prior_styles.get(actor_id)
        if counts is None:
            continue
        total = sum(counts.values())
        if total < MIN_PRIOR_ACTIONS:
            continue
        dominant, dom_count = counts.most_common(1)[0]
        if dom_count / total >= DOMINANT_STYLE_SHARE and this_action != dominant:
            broken.append(actor_id)
    return sorted(broken)


def alliance_formed_this_turn(actions: Sequence[ActionRecord], current_round: int) -> bool:
    """True if the alliance set changed between the last two resolved turns."""
    round_actions = [a for a in actions if a.round == current_round]
    keys = sorted({a.turn for a in round_actions})
    if len(keys) < 2:
        return bool(detect_alliances(round_actions))
    last_turn, prev_turn = keys[-1], keys[-2]
    now = _alliance_signature([a for a in round_actions if a.turn <= last_turn])
    before = _alliance_signature([a for a in round_actions if a.turn <= prev_turn])
    return now != before


def _alliance_signature(round_actions: Sequence[ActionRecord]) -> set[frozenset[str]]:
    return {frozenset(al.members) for al in detect_alliances(round_actions)}
