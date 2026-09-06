"""The action-name vocabulary the read-side insight engines tally moves by.

These engines (opponent stats, board signals, season/round analysis) turn a
resolved action log into per-action tallies. They are part of the PD game's
read side, so the action names belong to the PD game module — not to a literal
baked into each engine. This module is the single seam that pulls those names
off PD's own rules module, so the move vocabulary lives in one place behind
`pd_action_names()`.

Reads `ACTIONS` straight from `app.games.hoard_hurt_help.rules` — the leaf that
defines it — rather than through the `app.games` registry's `get()`. The
registry route used to run through `app.games` itself, which (via
`app.games.hoard_hurt_help.board_signals`, which calls `action_counts` below)
closed a real import cycle back to this module.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from app.engine.game_records import ActionRecord
from app.game_types import DEFAULT_GAME_TYPE
from app.games.hoard_hurt_help.rules import ACTIONS

PD_GAME_TYPE = DEFAULT_GAME_TYPE


def pd_action_names() -> tuple[str, ...]:
    """The PD game's ordered action names, e.g. ("HOARD", "HELP", "HURT").

    Reads PD's own `ACTIONS` tuple, so the engines that bucket the action log
    never hardcode the move vocabulary.
    """
    return ACTIONS


def action_counts(actions: Iterable[ActionRecord]) -> Counter[str]:
    """Tally a sequence of actions by action name.

    The one shared "count moves by type" helper for the read-side insight
    engines. Returns a `Counter` keyed by `ActionRecord.action`, so a missing
    action name reads as 0 — matching the `sum(1 for a in actions if a.action ==
    X)` pattern these engines used before. Filter the actions down to the slice
    you care about (a round, a player, a turn) before calling.
    """
    return Counter(a.action for a in actions)
