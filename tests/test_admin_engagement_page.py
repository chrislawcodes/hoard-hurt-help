"""The /admin/engagement page and the internal-account toggle."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.identity.milestones import record_milestone
from app.models.admin_audit_log import AdminAction, AdminAuditLog
from app.models.user import User, UserRole
from app.models.user_milestone import MilestoneKind
from tests.conftest import signed_in_cookies
from tests.factories import make_user


async def _admin(session_factory) -> User:
    async with session_factory() as db:
        user = await make_user(db, 0, handle="theadmin")
        user.role = UserRole.ADMIN
        await db.commit()
        return user


@pytest.mark.asyncio
async def test_anonymous_gets_401_not_403(reset_db, client) -> None:
    """Signed out is 401; signed in without the role is 403. They differ."""
    response = await client.get("/admin/engagement")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_signed_in_non_admin_gets_403(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        user_id = user.id

    response = await client.get(
        "/admin/engagement", cookies=signed_in_cookies(user_id)
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_the_page_renders_for_an_admin(reset_db, client) -> None:
    admin = await _admin(reset_db)
    response = await client.get(
        "/admin/engagement", cookies=signed_in_cookies(admin.id)
    )
    assert response.status_code == 200
    body = response.text
    assert "Engagement" in body
    # Both explanatory notes must be on the page, not just in the spec.
    assert "reconstructed from" in body, "the approximate-history note is missing"
    assert "unknown" in body, "the unknown-vs-direct note is missing"


@pytest.mark.asyncio
async def test_the_page_renders_on_an_empty_database(reset_db, client) -> None:
    """A quiet week must not divide by zero."""
    admin = await _admin(reset_db)
    response = await client.get(
        "/admin/engagement", cookies=signed_in_cookies(admin.id)
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_the_filter_form_shows_its_own_labels_not_reports(
    reset_db, client
) -> None:
    """Mirrors tests/test_admin.py's reports-page check. The filter form now
    renders through a shared partial (admin/_date_window_form.html) with this
    page's own labels/hint/clear-href set by {% set %} in engagement.html,
    right before the {% include %}. Pins that /admin/engagement keeps its own
    text after the dedup, and that /admin/reports's copy can't leak here."""
    admin = await _admin(reset_db)
    response = await client.get(
        "/admin/engagement", cookies=signed_in_cookies(admin.id)
    )
    assert response.status_code == 200
    body = response.text
    assert "Signed up from" in body
    assert "Signed up to" in body
    assert "Picks the signup cohort" in body
    # No filter set yet, so the partial's conditional Clear link is hidden.
    assert 'href="/admin/engagement">Clear</a>' not in body
    assert "Start date" not in body
    assert "Filters by match completion date" not in body

    response2 = await client.get(
        "/admin/engagement?start_date=2026-06-11", cookies=signed_in_cookies(admin.id)
    )
    assert response2.status_code == 200
    assert 'href="/admin/engagement">Clear</a>' in response2.text


@pytest.mark.asyncio
async def test_a_bad_date_is_rejected(reset_db, client) -> None:
    admin = await _admin(reset_db)
    response = await client.get(
        "/admin/engagement?start_date=not-a-date", cookies=signed_in_cookies(admin.id)
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_a_played_turn_shows_up_on_the_page(reset_db, client) -> None:
    admin = await _admin(reset_db)
    async with reset_db() as db:
        player = await make_user(db, 5, handle="aplayer")
        await db.commit()
        await record_milestone(db, player.id, MilestoneKind.PLAYED_TURN)
        await db.commit()

    response = await client.get(
        "/admin/engagement", cookies=signed_in_cookies(admin.id)
    )
    assert response.status_code == 200
    assert "Played a turn" in response.text


@pytest.mark.asyncio
async def test_the_toggle_moves_a_user_out_of_the_numbers(reset_db, client) -> None:
    admin = await _admin(reset_db)
    async with reset_db() as db:
        target = await make_user(db, 6, handle="mistaken")
        await db.commit()
        target_id = target.id

    response = await client.post(
        f"/admin/users/{target_id}/internal",
        data={"internal": "true"},
        cookies=signed_in_cookies(admin.id),
        follow_redirects=False,
    )
    assert response.status_code == 303

    async with reset_db() as db:
        stored = (
            await db.execute(select(User).where(User.id == target_id))
        ).scalar_one()
        assert stored.is_internal is True

        # And it is audit-logged like every other admin action.
        entry = (
            await db.execute(
                select(AdminAuditLog).where(AdminAuditLog.target_user_id == target_id)
            )
        ).scalar_one()
        assert entry.action is AdminAction.mark_internal


@pytest.mark.asyncio
async def test_the_toggle_works_both_ways(reset_db, client) -> None:
    """Marking and unmarking must be distinguishable in the audit trail."""
    admin = await _admin(reset_db)
    async with reset_db() as db:
        target = await make_user(db, 7, handle="backagain")
        target.is_internal = True
        await db.commit()
        target_id = target.id

    await client.post(
        f"/admin/users/{target_id}/internal",
        data={"internal": "false"},
        cookies=signed_in_cookies(admin.id),
        follow_redirects=False,
    )

    async with reset_db() as db:
        stored = (
            await db.execute(select(User).where(User.id == target_id))
        ).scalar_one()
        assert stored.is_internal is False
        entry = (
            await db.execute(
                select(AdminAuditLog).where(AdminAuditLog.target_user_id == target_id)
            )
        ).scalar_one()
        assert entry.action is AdminAction.unmark_internal


@pytest.mark.asyncio
async def test_the_toggle_renders_for_a_floor_admin_target(reset_db, client) -> None:
    """The control sits outside the floor-admin gate, deliberately.

    Floor admins are exactly the accounts the migration flags as internal, so
    hiding the control on them would hide it where it is most needed.
    """
    admin = await _admin(reset_db)
    response = await client.get(
        f"/admin/users/{admin.id}", cookies=signed_in_cookies(admin.id)
    )
    assert response.status_code == 200
    assert "/internal" in response.text
