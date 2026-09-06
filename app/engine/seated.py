"""Whether a player has not left — "is this player still seated?".

One home for the rule that a player counts as seated exactly when
``left_at`` is unset. It used to be written 40 times: 34 as the SQLAlchemy
clause ``Player.left_at.is_(None)`` and 6 as the in-memory check
``player.left_at is None`` — the same rule, spelled two ways, with no
guarantee the two spellings agreed. Leaf module (imports only the `Player`
model + sqlalchemy), so routes, games, and deps can all use it without an
import cycle.

This is not the same question as `app.engine.player_counts`, which is
specifically about *counting* seated players for a match. This predicate is
the thing that counting (and much else — candidate lookups, idle checks,
viewer context) is built on, so it lives here instead.
"""
from __future__ import annotations

from sqlalchemy import ColumnElement

from app.models.player import Player


def seated_filter() -> ColumnElement[bool]:
    """The SQLAlchemy clause form of "this player has not left": use inside
    a `.where(...)` alongside whatever other conditions the query needs.
    Same rule as `is_seated`, just written for the database instead of for
    a Python object already in hand.
    """
    return Player.left_at.is_(None)


def is_seated(player: Player) -> bool:
    """The in-memory form of "this player has not left": use once you
    already have a `Player` row loaded. Same rule as `seated_filter`, just
    written for a Python object instead of for the database.
    """
    return player.left_at is None
