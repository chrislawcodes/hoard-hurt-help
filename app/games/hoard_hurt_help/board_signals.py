"""Whole-board signals for Prisoner's Dilemma (hoard-hurt-help).

Alliances (mutual-help clusters), the round's cooperation "temperature", who is
surging, and which opponents broke their established pattern. Action-derived and
deterministic — no message text (v1).

These are PD-specific concepts (they read HELP/HURT semantics), so they live in
the PD game module, not the platform engine. The PD module exposes them through
`GameModule.board_signals(...)`; the platform never computes them itself.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Literal

from app.engine.action_vocab import action_counts
from app.engine.game_insights import detect_surging
from app.engine.game_records import ActionRecord, PlayerRecord
from app.schemas.agent import Alliance, BoardSignals

TemperatureLabel = Literal["hostile", "mixed", "cooperative"]

# This game's relationship moves: HELP (cooperate) and HURT (attack). Kept local
# so the board-signal helpers read PD's move vocabulary directly.
_HELP, _HURT = "HELP", "HURT"

# Tunable thresholds (module-level for tuning at scale).
ALLY_MIN_HELPS = 2
MAX_ALLIANCES = 5

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
    counts = action_counts(round_actions)
    helps = counts[_HELP]
    hurts = counts[_HURT]
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
        if a.action == _HELP and a.target_id is not None:
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


