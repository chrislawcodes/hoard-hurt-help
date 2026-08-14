"""The milestone table and the new users columns exist and behave.

These look like trivial schema tests, and two of them are guarding against a
failure mode that has no other symptom: if ``UserMilestone`` is not exported from
``app.models``, it never lands in ``Base.metadata``, every test builds a schema
without the table, and the recorder's advisory error handling swallows the
resulting failure. The suite goes green and the dashboard is permanently empty.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

# Imported from their own modules, deliberately, NOT from `app.models`. The
# registration test below asserts that app/models/__init__.py exports the model
# into Base.metadata; importing it from `app.models` here would turn that test
# into an ImportError at collection time, which passes for the wrong reason and
# would not catch a model registered under a different import style.
from app.models.user import User
from app.models.user_milestone import MilestoneKind, UserMilestone
from tests.factories import make_user


@pytest.mark.asyncio
async def test_model_is_exported_from_the_models_package() -> None:
    """app/models/__init__.py must export UserMilestone.

    This is the guard, and it has to be written this way. A declarative model
    registers itself with Base.metadata when its *module* is imported, so any test
    that imports `app.models.user_milestone` makes the table appear in
    `create_all` whether or not the package exports it — a schema assertion here
    passes even with the export deleted, which is worse than no test.

    What actually depends on the export is Alembic autogenerate and every
    production import path. Asserting on the package attribute is the only check
    that fails when the export line is removed.
    """
    import app.models as models_package

    assert hasattr(models_package, "UserMilestone")
    assert "UserMilestone" in models_package.__all__


@pytest.mark.asyncio
async def test_table_is_created_by_create_all(db) -> None:
    """And the model itself produces a real table in a create_all schema."""
    conn = await db.connection()
    tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    assert "user_milestones" in tables


@pytest.mark.asyncio
async def test_records_a_milestone(db) -> None:
    # PLAYED_TURN, not SIGNED_UP: creating the user already records SIGNED_UP via
    # the ORM listener, so adding it again by hand would collide with the unique
    # constraint rather than testing storage.
    user = await make_user(db)
    db.add(
        UserMilestone(
            user_id=user.id,
            milestone=MilestoneKind.PLAYED_TURN,
            reached_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()

    stored = (
        await db.execute(
            select(UserMilestone).where(
                UserMilestone.user_id == user.id,
                UserMilestone.milestone == MilestoneKind.PLAYED_TURN,
            )
        )
    ).scalar_one()
    assert stored.milestone is MilestoneKind.PLAYED_TURN
    assert stored.source_match_id is None


@pytest.mark.asyncio
async def test_same_milestone_twice_is_rejected(db) -> None:
    """The unique constraint is the idempotency guarantee the recorder relies on.

    The recorder catches this IntegrityError and treats it as "already recorded",
    so if the constraint were missing the table would fill with duplicates and
    every count on the dashboard would be inflated.
    """
    user = await make_user(db)
    for _ in range(2):
        db.add(
            UserMilestone(
                user_id=user.id,
                milestone=MilestoneKind.PICKED_HANDLE,
                reached_at=datetime.now(timezone.utc),
            )
        )
    with pytest.raises(IntegrityError):
        await db.commit()


@pytest.mark.asyncio
async def test_different_milestones_for_one_user_are_allowed(db) -> None:
    user = await make_user(db)
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            UserMilestone(
                user_id=user.id, milestone=MilestoneKind.PICKED_HANDLE, reached_at=now
            ),
            UserMilestone(
                user_id=user.id, milestone=MilestoneKind.PLAYED_TURN, reached_at=now
            ),
        ]
    )
    await db.commit()

    rows = (
        (await db.execute(select(UserMilestone).where(UserMilestone.user_id == user.id)))
        .scalars()
        .all()
    )
    # Two added here, plus the SIGNED_UP the listener recorded when the user was
    # created.
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_new_user_columns_default_to_unrecorded(db) -> None:
    """A user created without capture must be distinguishable from a direct visit.

    NULL means "we never captured this". An explicit "direct" means "capture ran
    and found nothing". Collapsing the two is what would make the largest row on
    the sources table silently mean "we failed to record it".
    """
    user = await make_user(db)
    await db.commit()

    stored = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    assert stored.first_utm_source is None
    assert stored.first_referrer_host is None
    assert stored.first_source_channel is None
    assert stored.is_internal is False


@pytest.mark.asyncio
async def test_source_match_id_is_stored(db) -> None:
    """Kept so smoke-test matches can be excluded when the page is read."""
    user = await make_user(db)
    db.add(
        UserMilestone(
            user_id=user.id,
            milestone=MilestoneKind.JOINED_MATCH,
            reached_at=datetime.now(timezone.utc),
            source_match_id="M_0001",
        )
    )
    await db.commit()

    stored = (
        await db.execute(
            select(UserMilestone).where(
                UserMilestone.milestone == MilestoneKind.JOINED_MATCH
            )
        )
    ).scalar_one()
    assert stored.source_match_id == "M_0001"
