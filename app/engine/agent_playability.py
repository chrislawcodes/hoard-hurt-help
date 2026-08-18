"""One definition of "can this agent take a seat", in the two forms callers need.

WHY THIS EXISTS. The rule was written out by hand in a dozen places. Eleven of
them said *owner + kind=AI + not archived + status=ACTIVE*; the join path said
*owner + kind=AI + not archived* and dropped the status. Nobody decided that —
there was no function to call, so each caller wrote the query from whatever
definition was in their head. The result was a live bug: a PAUSED agent appeared
in the join picker, took a seat, and was then skipped by every turn-serving
query, so the overdue sweeper defaulted its moves (HOARD) for the whole match
while the owner saw no error anywhere.

TWO FORMS, ONE RULE. A ``WHERE`` clause and an ``if`` cannot be the same line of
code, so this module deliberately exposes both and pins them together:

- :func:`playable_agent_filter` — SQL, for queries that select agents to serve.
- :func:`seat_block` — for an already-loaded Agent, returning WHY it cannot play
  plus where to fix it, so the picker and the seating route say the same thing.

``tests/test_agent_playability.py`` builds an agent in every combination of kind,
status and archived-ness and asserts the two forms agree on all of them. That
test is the actual guard: adding a new way for an agent to be unplayable and
updating only one form fails immediately, instead of drifting the way the
original rule did.

SCOPE. This owns the agent's own state only — kind, status, archived. Whether the
owner's AI is reachable is a separate question with its own single answer
(``play_verdict`` in ``provider_readiness.py``); a seat needs both. Missing
``current_version_id`` is still checked at its own call sites, since it needs the
joined version row rather than the Agent alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import ColumnElement

from app.models.agent import Agent, AgentKind, AgentStatus


@dataclass(frozen=True)
class SeatBlock:
    """Why an agent cannot take a seat, in the words a user should read.

    ``fix_path`` is a link rather than instructions on purpose: the join picker
    already sends a broken agent to ``/me/agents/<id>`` ("fix it") instead of
    spelling out the steps, and the control the user needs is on that page.
    """

    reason: str
    fix_label: str | None = None
    fix_path: str | None = None


def playable_agent_filter() -> list[ColumnElement[bool]]:
    """SQL conditions for "this agent can be handed a turn".

    Ownership is deliberately NOT included — every caller scopes by a different
    column (``Agent.user_id``, ``connection.user_id``), and that half never
    drifted. What drifted is the agent-state half, so that is what lives here.
    """
    return [
        Agent.kind == AgentKind.AI,
        Agent.status == AgentStatus.ACTIVE,
        Agent.archived_at.is_(None),
    ]


def seat_block(agent: Agent) -> SeatBlock | None:
    """Why *agent* cannot take a seat right now, or ``None`` if it can.

    Ordered most-permanent first, so a user is told the thing they have to deal
    with rather than the first box that happens to be unticked.
    """
    if agent.archived_at is not None:
        return SeatBlock("Archived")
    if agent.kind is not AgentKind.AI:
        return SeatBlock("Not an AI agent")
    if agent.status is not AgentStatus.ACTIVE:
        return SeatBlock("Paused", "resume it", f"/me/agents/{agent.id}")
    return None
