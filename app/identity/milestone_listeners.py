"""Record milestones from ORM events, so no call site has to remember to.

Earlier designs kept a hand-written list of the places that create a user, an
agent or a player. Three review rounds each found sites the list had missed —
human agents are built in their own module, players are seated from three
different places, and one connection path sets its field inside a values dict
where no text search would find it.

Listening to the ORM removes the list entirely: any code that inserts one of
these rows records the milestone, including code nobody has written yet.

Two different kinds of event are involved, and mixing them up fails silently:

* ``after_insert`` is a **mapper** event and must be bound to the model classes.
  Binding it to ``Session`` is accepted without raising and then fires zero times.
* ``after_flush_postexec`` is a **session** event and must be bound to ``Session``.

``after_insert`` runs inside the flush and cannot do the work itself, so it only
collects; the postexec hook writes what was collected and clears the collection.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session, object_session

from app.identity.milestones import build_row, record_pending_sync
from app.models.agent import Agent, AgentKind
from app.models.player import Player
from app.models.user import User
from app.models.user_milestone import MilestoneKind

_PENDING_KEY = "pending_milestones"
_PENDING_PLAYER_KEY = "pending_player_milestones"

# An agent is a way to play. Which kind matters, and it gets its own milestone
# rather than a column on a shared one: manual play is the default for a new
# user, so a single write-once row would record "human" for almost everyone and
# leave the AI population looking empty.
_AGENT_KIND_MILESTONES = {
    AgentKind.AI: MilestoneKind.SET_UP_AI_AGENT,
    AgentKind.HUMAN: MilestoneKind.SET_UP_HUMAN_PLAY,
    # AgentKind.BOT is deliberately absent — a platform bot is not a user journey.
}


def _pending(session: Session, key: str) -> list[dict[str, Any]]:
    collected = session.info.setdefault(key, [])
    assert isinstance(collected, list)
    return collected


def _on_user_insert(_mapper: Any, _connection: Any, target: User) -> None:
    # Accounts that are ours are not signups. Reading the flag rather than the
    # bots account's id keeps app/engine out of app/identity — importing one
    # constant from the bots engine pulled 16 engine modules into app/db, which
    # is the most-imported module in the repo.
    if target.is_internal:
        return
    session = object_session(target)
    if session is None:
        return
    _pending(session, _PENDING_KEY).append(
        build_row(target.id, MilestoneKind.SIGNED_UP)
    )


def _on_agent_insert(_mapper: Any, _connection: Any, target: Agent) -> None:
    milestone = _AGENT_KIND_MILESTONES.get(target.kind)
    if milestone is None:
        return
    session = object_session(target)
    if session is None:
        return
    _pending(session, _PENDING_KEY).append(build_row(target.user_id, milestone))


def _on_player_insert(_mapper: Any, _connection: Any, target: Player) -> None:
    """Collect separately: player rows need the internal filter applied later.

    Unlike a User or an Agent, the Player row does not carry the owner's flag, so
    deciding this needs one lookup after the flush rather than an attribute read.
    """
    session = object_session(target)
    if session is None:
        return
    _pending(session, _PENDING_PLAYER_KEY).append(
        build_row(
            target.user_id,
            MilestoneKind.JOINED_MATCH,
            source_match_id=target.match_id,
        )
    )


def _drop_internal_rows(
    session: Session, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Remove player milestones belonging to accounts that are ours.

    Platform bots are seated as real Player rows owned by the bots account, so
    without this every bot in every match reads as that account joining a match.
    Widened from "the bots account" to "any internal account" so house rigs and
    the dev login stop writing rows the page discards anyway — same single query.
    """
    if not rows:
        return rows
    user_ids = {row["user_id"] for row in rows}
    internal_ids = set(
        session.scalars(
            select(User.id).where(User.is_internal.is_(True), User.id.in_(user_ids))
        ).all()
    )
    if not internal_ids:
        return rows
    return [row for row in rows if row["user_id"] not in internal_ids]


def _on_after_flush_postexec(session: Session, _flush_context: Any) -> None:
    rows = _pending(session, _PENDING_KEY)
    player_rows = _pending(session, _PENDING_PLAYER_KEY)
    if not rows and not player_rows:
        return
    # Clear before writing. The write itself flushes nothing, but leaving the
    # collection in place would re-attempt every earlier row on every later flush
    # of the same session — measured at 66 insert attempts for 2 milestones.
    session.info[_PENDING_KEY] = []
    session.info[_PENDING_PLAYER_KEY] = []
    record_pending_sync(session, rows + _drop_internal_rows(session, player_rows))


def install_milestone_listeners() -> None:
    """Register the milestone listeners once, globally, idempotently.

    Called at import from app.db, not at app startup: the test client mounts the
    app with no lifespan, so a startup-time registration would leave every test
    running with no listeners attached — passing, and proving nothing.
    """
    for model, handler in (
        (User, _on_user_insert),
        (Agent, _on_agent_insert),
        (Player, _on_player_insert),
    ):
        # Mapper event: bound to the model class. Binding this to Session is
        # accepted silently and never fires.
        if not event.contains(model, "after_insert", handler):
            event.listen(model, "after_insert", handler)

    # Session event: bound to Session.
    if not event.contains(Session, "after_flush_postexec", _on_after_flush_postexec):
        event.listen(Session, "after_flush_postexec", _on_after_flush_postexec)
