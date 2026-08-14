"""Write a user's milestone rows, once each, without ever breaking the caller.

Two entry points on purpose. Recording is triggered from two very different
places, and one implementation cannot serve both:

* ``record_milestone`` — async, for the handful of sites that call it directly
  from a request handler, where a normal ``begin_nested()`` savepoint works.
* ``record_pending_sync`` — sync, for the ORM listeners. Listener callbacks run
  inside a flush and cannot await anything, so they take the sync path.

Both are **advisory**: a milestone is reporting, never game state. A failure here
must never break the request that triggered it — someone joining a match must not
see an error because an analytics row could not be written.

The savepoint is not decoration. Three simpler shapes were tried and refuted by
running them against this stack:

* ``db.add`` alone lets the IntegrityError surface at the *caller's* commit.
* ``add`` + ``flush`` in a try leaves the session in ``PendingRollbackError``.
* ``db.begin_nested()`` is a **no-op inside a flush** — SQLAlchemy skips its flush
  loop while ``session._flushing`` is set — so on the listener path the row is
  inserted unprotected and a duplicate raises out of the caller's commit, which is
  exactly what the savepoint exists to prevent.

Hence the listener path takes a savepoint on the *connection*
(``session.connection()`` → ``conn.begin_nested()``) and inserts through Core.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.user_milestone import MilestoneKind, UserMilestone

logger = logging.getLogger(__name__)


def build_row(
    user_id: int,
    milestone: MilestoneKind,
    *,
    source_match_id: str | None = None,
    reached_at: datetime | None = None,
) -> dict[str, Any]:
    """One milestone row, ready for a Core insert."""
    return {
        "user_id": user_id,
        "milestone": milestone,
        "reached_at": reached_at or datetime.now(timezone.utc),
        "source_match_id": source_match_id,
    }


def record_pending_sync(session: Session, rows: list[dict[str, Any]]) -> None:
    """Insert milestone rows from inside a flush. Never raises to the caller."""
    if not rows:
        return
    connection = session.connection()
    for row in rows:
        try:
            # A real savepoint: begin_nested() on the *connection* is not skipped
            # the way the session-level call is while a flush is in progress.
            with connection.begin_nested():
                connection.execute(insert(UserMilestone), row)
        except IntegrityError:
            # Already recorded. The unique constraint is the idempotency guard, so
            # this is the expected outcome every time after the first, not an error.
            continue
        except (SQLAlchemyError, ValueError):
            # fail-open: advisory only — a milestone is reporting, not game state,
            # and must never break the write that triggered it. ValueError is
            # included because an over-length value raises ValueError, not a
            # SQLAlchemyError, and would otherwise escape.
            logger.exception(
                "milestone write failed user_id=%s milestone=%s",
                row.get("user_id"),
                row.get("milestone"),
            )


async def record_milestone(
    db: AsyncSession,
    user_id: int,
    milestone: MilestoneKind,
    *,
    source_match_id: str | None = None,
) -> None:
    """Record one milestone from async request code. Never raises to the caller."""
    row = build_row(user_id, milestone, source_match_id=source_match_id)
    try:
        async with db.begin_nested():
            await db.execute(insert(UserMilestone), row)
    except IntegrityError:
        # Already recorded — see record_pending_sync.
        return
    except (SQLAlchemyError, ValueError):
        # fail-open: advisory only.
        logger.exception(
            "milestone write failed user_id=%s milestone=%s", user_id, milestone
        )
