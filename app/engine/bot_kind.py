"""The one value-level bot-kind predicate.

Leaf module (imports only the `AgentKind` model), so `arena.py`,
`turn_drivers.py`, and `user_match_start.py` can all use it without an import
cycle. The DB-level check in `turn_drivers` and the inline check in `arena`
both delegate here.
"""

from __future__ import annotations

from app.models.agent import AgentKind


def is_bot_kind(kind: object) -> bool:
    """True for a scripted bot seat (enum member or its raw string value)."""
    return kind in (AgentKind.BOT, AgentKind.BOT.value)
