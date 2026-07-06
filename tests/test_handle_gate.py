"""Integration tests for the handle gate and the /me/handle form."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.models import User
from tests.factories import make_user
from tests.conftest import signed_in_cookies as _cookies


async def _make_user(reset_db, *, i: int = 0, handle: str | None = None) -> User:
    async with reset_db() as db:
        u = await make_user(db, i)
        # Fully control the handle here (the factory sets a default one): None to
        # exercise the gate, or a specific value to test uniqueness/cooldown.
        u.handle = handle
        u.handle_key = handle.lower() if handle else None
        u.handle_changed_at = datetime.now(timezone.utc) if handle else None
        await db.commit()
        await db.refresh(u)
        return u


async def test_owner_without_handle_is_gated_on_dashboard(reset_db, client):
    user = await _make_user(reset_db)
    resp = await client.get("/me/agents", cookies=_cookies(user.id))
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/me/handle?next=")


async def test_user_with_handle_passes_the_gate(reset_db, client):
    user = await _make_user(reset_db, handle="coingoblin")
    resp = await client.get("/me/agents", cookies=_cookies(user.id))
    assert resp.status_code == 200


async def test_handle_form_is_reachable_for_handleless_user(reset_db, client):
    user = await _make_user(reset_db)
    resp = await client.get("/me/handle", cookies=_cookies(user.id))
    assert resp.status_code == 200
    assert 'name="handle"' in resp.text


async def test_post_saves_handle_and_redirects_to_next(reset_db, client):
    user = await _make_user(reset_db)
    resp = await client.post(
        "/me/handle",
        data={"handle": "ZeusMaster", "next": "/me/agents"},
        cookies=_cookies(user.id),
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/me/agents"

    async with reset_db() as db:
        refreshed = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    assert refreshed.handle == "ZeusMaster"
    assert refreshed.handle_key == "zeusmaster"


async def test_post_rejects_taken_handle_case_insensitively(reset_db, client):
    await _make_user(reset_db, i=1, handle="taken")
    user = await _make_user(reset_db, i=2)
    resp = await client.post(
        "/me/handle",
        data={"handle": "Taken", "next": "/me/agents"},
        cookies=_cookies(user.id),
    )
    assert resp.status_code == 200
    assert "taken" in resp.text.lower()

    async with reset_db() as db:
        refreshed = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    assert refreshed.handle is None


async def test_post_within_cooldown_is_blocked(reset_db, client):
    user = await _make_user(reset_db, handle="firstname")
    resp = await client.post(
        "/me/handle",
        data={"handle": "secondname", "next": "/me/agents"},
        cookies=_cookies(user.id),
    )
    assert resp.status_code == 200
    assert "change it again" in resp.text.lower()

    async with reset_db() as db:
        refreshed = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    assert refreshed.handle == "firstname"
