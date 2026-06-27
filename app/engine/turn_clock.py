"""Shared turn-loop clock primitives.

One home for the turn drivers' poll cadence and UTC-now helper so the
simultaneous (`scheduler_turn_loop`) and sequential (`turn_drivers`) drivers
stop re-defining them. Leaf module — imports only the stdlib, so it is safe to
import from either driver without a cycle.
"""
from __future__ import annotations

from datetime import datetime, timezone

# How often a turn wait-loop re-checks for submissions/messages before its
# deadline. Shared by both turn drivers.
SUBMIT_POLL_SECONDS = 0.25


def now_utc() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)
