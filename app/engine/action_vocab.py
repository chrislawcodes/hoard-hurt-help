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

PD_GAME_TYPE = "hoard-hurt-help"


def pd_action_names() -> tuple[str, ...]:
    """The PD game's ordered action names, e.g. ("HOARD", "HELP", "HURT").

    Read from the registered game module via `app.games.get`, so the engines
    that bucket the action log never hardcode the move vocabulary.
    """
    from app.games import get

    return tuple(get(PD_GAME_TYPE).action_names())
