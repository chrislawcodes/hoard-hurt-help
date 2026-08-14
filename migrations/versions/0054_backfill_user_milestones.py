"""reconstruct milestones for accounts that existed before this feature

Approximate on purpose, and wrong in both directions. The page says so.

It UNDERCOUNTS, because the app destroys the evidence: incomplete connection
setups are garbage collected, held seats are deleted within ~15 minutes, agents
with no game history are hard-deleted rather than archived, an admin match delete
removes players and turns, and a handle reset nulls the handle columns outright.
People who abandoned at those points cannot be recovered.

It would OVERCOUNT played_turn if we were not careful: autopilot plays on behalf
of someone who walked away, and those rows carry ``was_defaulted = false`` and a
real timestamp, so they are indistinguishable from a genuine move. We exclude any
seat that ever went on autopilot (``players.autopilot_at``). That over-excludes —
genuine turns played before a seat went on autopilot are dropped too — which is
the conservative direction.

Every insert is guarded by NOT EXISTS, so re-running is a no-op rather than a
constraint violation.

Rehearse with ``scripts/preview_milestone_backfill.py --db copy.db --dry-run``
before merging. Merging deploys: railway.json runs migrations as a
preDeployCommand, so there is no moment in between.

Revision ID: 0054
Revises: 0053
Create Date: 2026-08-13
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0054"
down_revision: Union[str, None] = "0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _insert(select_sql: str) -> str:
    """Wrap a source query so it only inserts milestones that are missing."""
    return f"""
        INSERT INTO user_milestones (user_id, milestone, reached_at, source_match_id)
        SELECT src.user_id, src.milestone, src.reached_at, src.source_match_id
        FROM ({select_sql}) AS src
        WHERE NOT EXISTS (
            SELECT 1 FROM user_milestones m
            WHERE m.user_id = src.user_id AND m.milestone = src.milestone
        )
    """


# Every account that exists reached signed_up, by definition.
_SIGNED_UP = """
    SELECT id AS user_id, 'signed_up' AS milestone,
           created_at AS reached_at, NULL AS source_match_id
    FROM users
"""

# handle_changed_at is when it was last set, not first — the best surviving proxy.
# A user whose handle was reset by an admin has both columns nulled and cannot be
# recovered at all.
_PICKED_HANDLE = """
    SELECT id AS user_id, 'picked_handle' AS milestone,
           COALESCE(handle_changed_at, created_at) AS reached_at,
           NULL AS source_match_id
    FROM users
    WHERE handle_key IS NOT NULL
"""

_SET_UP_AI = """
    SELECT user_id, 'set_up_ai_agent' AS milestone,
           MIN(created_at) AS reached_at, NULL AS source_match_id
    FROM agents WHERE kind = 'ai' GROUP BY user_id
"""

_SET_UP_HUMAN = """
    SELECT user_id, 'set_up_human_play' AS milestone,
           MIN(created_at) AS reached_at, NULL AS source_match_id
    FROM agents WHERE kind = 'human' GROUP BY user_id
"""

_AI_CONNECTED = """
    SELECT user_id, 'ai_connected' AS milestone,
           MIN(first_connected_at) AS reached_at, NULL AS source_match_id
    FROM connections
    WHERE first_connected_at IS NOT NULL
    GROUP BY user_id
"""

# Bots are seated as real Player rows owned by the platform account; that account
# is flagged internal by migration 0053 and filtered out of the page, but there is
# no reason to write the rows in the first place.
_JOINED_MATCH = """
    SELECT p.user_id, 'joined_match' AS milestone,
           MIN(p.joined_at) AS reached_at, NULL AS source_match_id
    FROM players p
    JOIN users u ON u.id = p.user_id
    WHERE u.google_sub <> 'platform:bots'
    GROUP BY p.user_id
"""

# A genuine move: not a missed deadline, not a connector fallback (both carry
# was_defaulted), not a row with no timestamp, and not from a seat that ever went
# on autopilot.
_PLAYED_TURN = """
    SELECT p.user_id, 'played_turn' AS milestone,
           MIN(ts.submitted_at) AS reached_at, NULL AS source_match_id
    FROM turn_submissions ts
    JOIN players p ON p.id = ts.player_id
    JOIN users u ON u.id = p.user_id
    WHERE NOT ts.was_defaulted
      AND ts.submitted_at IS NOT NULL
      AND p.autopilot_at IS NULL
      AND u.google_sub <> 'platform:bots'
    GROUP BY p.user_id
"""


def upgrade() -> None:
    for source in (
        _SIGNED_UP,
        _PICKED_HANDLE,
        _SET_UP_AI,
        _SET_UP_HUMAN,
        _AI_CONNECTED,
        _JOINED_MATCH,
        _PLAYED_TURN,
    ):
        op.execute(_insert(source))


def downgrade() -> None:
    # Everything this migration wrote is reconstructed data, so removing all of it
    # is the honest inverse. Rows recorded live after the deploy are indis-
    # tinguishable from backfilled ones, which is precisely why migration 0052's
    # downgrade drops the whole table rather than trying to unpick it.
    op.execute("DELETE FROM user_milestones")
