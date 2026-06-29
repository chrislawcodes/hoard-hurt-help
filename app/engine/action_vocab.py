"""The action-name vocabulary the read-side insight engines tally moves by.

These engines (opponent stats, board signals, season/round analysis) turn a
resolved action log into per-action tallies. They are part of the PD game's
read side, so the action names belong to the PD game module — not to a literal
baked into each engine. This module is the single seam that pulls those names
off the registered `hoard-hurt-help` module, so the move vocabulary lives in one
place behind the `GameModule.action_names()` contract.

The import-time `from app.games import get` inside the function (not at module
top) keeps the dependency one-directional: the game module pulls in the
resolver/rules engines, never these read-side engines, so there is no cycle.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from app.engine.game_records import ActionRecord
from app.game_types import DEFAULT_GAME_TYPE

PD_GAME_TYPE = DEFAULT_GAME_TYPE


def pd_action_names() -> tuple[str, ...]:
    """The PD game's ordered action names, e.g. ("HOARD", "HELP", "HURT").

    Read from the registered game module via `app.games.get`, so the engines
    that bucket the action log never hardcode the move vocabulary.
    """
    from app.games import get

    return tuple(get(PD_GAME_TYPE).action_names())


def action_counts(actions: Iterable[ActionRecord]) -> Counter[str]:
    """Tally a sequence of actions by action name.

    The one shared "count moves by type" helper for the read-side insight
    engines. Returns a `Counter` keyed by `ActionRecord.action`, so a missing
    action name reads as 0 — matching the `sum(1 for a in actions if a.action ==
    X)` pattern these engines used before. Filter the actions down to the slice
    you care about (a round, a player, a turn) before calling.
    """
    return Counter(a.action for a in actions)
