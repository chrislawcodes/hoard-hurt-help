"""The milestone recorder and its ORM listeners.

Three earlier mechanism designs were refuted by running them, and each failed the
same way: the caller's save reported success and zero rows landed. The tests here
are written to catch that shape specifically, which is harder than it looks —
see ``test_milestone_insert_is_followed_by_a_commit`` for why the obvious test
cannot fail on this test database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, func, select

from app.identity.milestones import build_row, record_milestone
from app.models.agent import Agent, AgentKind
from app.models.user import User
from app.models.user_milestone import MilestoneKind, UserMilestone
from tests.factories import make_user


async def _milestones(db, user_id: int) -> set[MilestoneKind]:
    rows = (
        (
            await db.execute(
                select(UserMilestone.milestone).where(UserMilestone.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


@pytest.mark.asyncio
async def test_creating_a_user_records_signed_up(db) -> None:
    """No call site asks for this — the ORM listener does it."""
    user = await make_user(db)
    await db.commit()
    assert MilestoneKind.SIGNED_UP in await _milestones(db, user.id)


@pytest.mark.asyncio
async def test_ai_and_human_agents_record_different_milestones(db) -> None:
    """The split exists so the AI population does not vanish.

    Manual play is the default for a brand-new user. On a single write-once
    milestone carrying a kind, almost every user would record "human" first and
    the AI-agent count would collapse toward zero.
    """
    ai_owner = await make_user(db, 1)
    human_owner = await make_user(db, 2)
    db.add_all(
        [
            Agent(user_id=ai_owner.id, kind=AgentKind.AI, name="Bot One", game="hoard-hurt-help"),
            Agent(
                user_id=human_owner.id,
                kind=AgentKind.HUMAN,
                name="Person",
                game="hoard-hurt-help",
            ),
        ]
    )
    await db.commit()

    assert MilestoneKind.SET_UP_AI_AGENT in await _milestones(db, ai_owner.id)
    assert MilestoneKind.SET_UP_HUMAN_PLAY in await _milestones(db, human_owner.id)
    assert MilestoneKind.SET_UP_AI_AGENT not in await _milestones(db, human_owner.id)


@pytest.mark.asyncio
async def test_platform_bot_agents_record_nothing(db) -> None:
    """A platform bot is not a user journey."""
    owner = await make_user(db)
    db.add(
        Agent(user_id=owner.id, kind=AgentKind.BOT, name="Scripted", game="hoard-hurt-help")
    )
    await db.commit()

    reached = await _milestones(db, owner.id)
    assert MilestoneKind.SET_UP_AI_AGENT not in reached
    assert MilestoneKind.SET_UP_HUMAN_PLAY not in reached


@pytest.mark.asyncio
async def test_the_bots_account_does_not_record_a_signup(db) -> None:
    """Built through the real helper, not by hand.

    The listener decides this from users.is_internal, which get_or_create_bots_user
    sets. An earlier version of this test constructed the row itself and left the
    flag off, so it was asserting against an account the app never creates.
    """
    from app.engine.bots.seating import get_or_create_bots_user

    bots_user = await get_or_create_bots_user(db)
    await db.commit()
    assert await _milestones(db, bots_user.id) == set()


@pytest.mark.asyncio
async def test_recording_the_same_milestone_twice_leaves_one_row(db) -> None:
    user = await make_user(db)
    await db.commit()

    # signed_up already exists from the listener; ask for it again explicitly.
    await record_milestone(db, user.id, MilestoneKind.SIGNED_UP)
    await db.commit()

    count = await db.scalar(
        select(func.count())
        .select_from(UserMilestone)
        .where(
            UserMilestone.user_id == user.id,
            UserMilestone.milestone == MilestoneKind.SIGNED_UP,
        )
    )
    assert count == 1


@pytest.mark.asyncio
async def test_a_duplicate_does_not_break_the_callers_transaction(db) -> None:
    """The whole point of the savepoint.

    A collision on an advisory row must not poison the session the caller is using
    for real work — their own rows still have to commit.
    """
    user = await make_user(db)
    await db.commit()

    await record_milestone(db, user.id, MilestoneKind.SIGNED_UP)  # duplicate

    # The caller's own write must still land after the swallowed collision.
    db.add(Agent(user_id=user.id, kind=AgentKind.AI, name="After", game="hoard-hurt-help"))
    await db.commit()

    stored = (
        await db.execute(select(Agent).where(Agent.user_id == user.id))
    ).scalar_one()
    assert stored.name == "After"


@pytest.mark.asyncio
async def test_milestone_insert_is_followed_by_a_commit(db, engine) -> None:
    """Assert the write is committed, by watching the order of database events.

    The obvious version of this test — write, then read it back from a "fresh"
    session — CANNOT FAIL here. The in-memory test database uses a single shared
    connection, so a writer that never commits is still visible to every other
    session. An earlier revision specified exactly that assertion and it passed
    against an implementation that never committed.

    Watching for a commit after the insert is the check that actually
    discriminates, and it discriminates on SQLite.
    """
    timeline: list[str] = []

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _watch_statements(_conn, _cursor, statement, _params, _ctx, _many) -> None:
        if statement.lstrip().lower().startswith("insert into user_milestones"):
            timeline.append("insert")

    @event.listens_for(engine.sync_engine, "commit")
    def _watch_commit(_conn) -> None:
        timeline.append("commit")

    try:
        await make_user(db)
        await db.commit()
    finally:
        event.remove(engine.sync_engine, "after_cursor_execute", _watch_statements)
        event.remove(engine.sync_engine, "commit", _watch_commit)

    assert "insert" in timeline, "no milestone row was inserted at all"
    assert "commit" in timeline[timeline.index("insert") :], (
        "the milestone was inserted but never committed — this would pass a "
        "read-back assertion on SQLite and lose rows on Postgres"
    )


@pytest.mark.asyncio
async def test_repeated_flushes_do_not_re_attempt_earlier_rows(db) -> None:
    """The collection must be cleared once written.

    Left in place, every later flush of the same session re-attempts every earlier
    row. It stays correct — the unique constraint rejects the duplicates — but at
    the cost of a wasted round trip per row per flush.
    """
    inserts = 0
    engine = db.get_bind()

    @event.listens_for(engine, "after_cursor_execute")
    def _count(_conn, _cursor, statement, _params, _ctx, _many) -> None:
        nonlocal inserts
        if statement.lstrip().lower().startswith("insert into user_milestones"):
            inserts += 1

    try:
        for i in range(5):
            await make_user(db, i)
            await db.flush()
        await db.commit()
    finally:
        event.remove(engine, "after_cursor_execute", _count)

    assert inserts == 5, f"expected one insert per user, got {inserts}"


@pytest.mark.asyncio
async def test_recorder_survives_a_bad_row_without_raising(db) -> None:
    """Advisory means advisory: a broken write logs and the caller continues."""
    user = await make_user(db)
    await db.commit()

    # A user_id that violates the foreign key. This must not surface.
    await record_milestone(db, 10_000_000, MilestoneKind.PLAYED_TURN)

    db.add(Agent(user_id=user.id, kind=AgentKind.AI, name="Still Here", game="hoard-hurt-help"))
    await db.commit()
    assert (
        await db.execute(select(Agent).where(Agent.user_id == user.id))
    ).scalar_one().name == "Still Here"


@pytest.mark.asyncio
async def test_build_row_defaults_the_timestamp(db) -> None:
    row = build_row(1, MilestoneKind.SIGNED_UP)
    assert row["reached_at"] is not None
    assert row["source_match_id"] is None


@pytest.mark.asyncio
async def test_explicit_recorder_stores_the_match(db) -> None:
    user = await make_user(db)
    await db.commit()
    await record_milestone(
        db, user.id, MilestoneKind.PLAYED_TURN, source_match_id="M_0042"
    )
    await db.commit()

    stored = (
        await db.execute(
            select(UserMilestone).where(
                UserMilestone.milestone == MilestoneKind.PLAYED_TURN
            )
        )
    ).scalar_one()
    assert stored.source_match_id == "M_0042"


def test_listeners_are_registered_on_the_right_event_kinds() -> None:
    """after_insert is a MAPPER event; after_flush_postexec is a SESSION event.

    Binding after_insert to Session is accepted without raising and then fires
    zero times — a green suite and a permanently empty table. This asserts the
    binding rather than trusting it.
    """
    from sqlalchemy.orm import Session

    import app.db  # noqa: F401  (importing registers the listeners)
    from app.identity import milestone_listeners as ml
    from app.models.player import Player

    assert event.contains(User, "after_insert", ml._on_user_insert)
    assert event.contains(Agent, "after_insert", ml._on_agent_insert)
    assert event.contains(Player, "after_insert", ml._on_player_insert)
    assert event.contains(
        Session, "after_flush_postexec", ml._on_after_flush_postexec
    )
