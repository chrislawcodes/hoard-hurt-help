"""Structured operational-event logging helper.

Every call emits a single line to the standard Python logging system with a
consistent machine-greppable prefix so operators can find all operational
failure events with ``grep ops_event=``.

Format (all on one line)::

    ops_event=<event> [key=value ...] | <human message>

Keys are sorted alphabetically for stable output. Values are str()-ed; no JSON
dependency.

Registered event names
----------------------
- bot_profile_invalid        — a bot's profile failed validation at play time
                               (app/engine/sims/service.py)
- connector_fallback_move    — a submission/message arrived flagged as a
                               connector fallback (app/routes/agent_api.py)
- lobby_reconciliation_failed — lobby DB error during overdue-game reconciliation
                               (app/routes/web_lobby.py)
- match_cancelled            — a match was cancelled by the platform; always
                               carries ``reason=`` explaining why
                               (app/engine/scheduler.py, app/engine/arena.py,
                               app/db_bootstrap.py)
- poller_subsystem_failed    — a background poller subsystem raised an exception
                               (app/engine/scheduler.py)
- poller_subsystem_persistent_failure — same subsystem has failed N times in a row
                               (app/engine/scheduler.py)
- practice_arena_seating_failed — bot seating rollback in Practice Arena creation
                               (app/engine/arena.py)
- replay_fallback            — DB error building robot-circle replay; fell back
                               to sample data (app/routes/web_lobby.py)
- turn_loop_crashed          — a game's fire-and-forget turn loop raised and the
                               match froze; also persisted to request_incidents
                               keyed by match_id (app/engine/scheduler.py)
"""

from __future__ import annotations

import logging


def log_ops_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    **fields: object,
) -> None:
    """Emit one structured ops-event log line.

    The line has the form::

        ops_event=<event> [key=value ...] | <message>

    ``fields`` are sorted by key for stable, grep-friendly output.
    Each value is str()-ed; no JSON dependency.

    Parameters
    ----------
    logger:
        The module-level logger to emit through (preserves the calling
        module's name in the log record).
    level:
        Standard ``logging`` level integer (e.g. ``logging.ERROR``).
    event:
        Stable snake_case event name from the registered list in this module's
        docstring.
    message:
        Human-readable description of what happened.
    **fields:
        Additional key/value context (match_id, reason, agent_id, …).
    """
    parts = [f"ops_event={event}"]
    for key in sorted(fields):
        parts.append(f"{key}={fields[key]}")
    prefix = " ".join(parts)
    logger.log(level, "%s | %s", prefix, message)
