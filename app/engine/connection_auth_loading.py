"""Shared eager-load option for connection-auth lookups.

Every place that loads a ``Connection`` to authorize play must also see whether
the owning user is disabled, but it must not pull the whole ``User`` row on the
hot agent-poll path. The single option below joins ``Connection.user`` and loads
only ``User.disabled_at`` — one shared definition so the eager-load stays
identical at every call site.
"""

from __future__ import annotations

from sqlalchemy.orm import joinedload
from sqlalchemy.orm.interfaces import ORMOption

from app.models.connection import Connection
from app.models.user import User


def connection_user_load_options() -> ORMOption:
    """Eager-load only ``connection.user.disabled_at`` for an auth check.

    Returns the loader option to pass to ``select(Connection).options(...)``.
    """
    return joinedload(Connection.user).load_only(User.disabled_at)
