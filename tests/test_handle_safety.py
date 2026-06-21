"""Tests for agent-name screening and admin handle reset."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.identity import word_filter
from app.models import Base, GameState, Match, Player, User
from app.models.user import UserRole
from tests.factories import make_agent, make_user


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)
    monkeypatch.setattr(settings, "admin_emails", "admin@test.com")

    yield test_factory
    await test_engine.dispose()


def _cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(json.dumps({"user_id": user_id}).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


# --- agent-name screening ----------------------------------------------------


def test_validate_agent_name_accepts_clean_name() -> None:
    assert word_filter.contains_blocked("Coalition Seeker") is False


def test_validate_agent_name_rejects_blocked_without_echo() -> None:
    bad = "shitbot"
    assert word_filter.contains_blocked(bad) is True


# --- admin handle reset ------------------------------------------------------


async def test_admin_reset_clears_handle_and_keeps_history(reset_db, client):
    async with reset_db() as db:
        admin = User(
            google_sub="sub-admin",
            email="admin@test.com",
            handle="boss",
            handle_key="boss",
            role=UserRole.ADMIN,
        )
        db.add(admin)
        target = await make_user(db, 5)  # handle "agent5"
        target.handle = "rudeword"
        target.handle_key = "rudeword"
        target.handle_changed_at = datetime.now(timezone.utc)
        agent, _ = await make_agent(db, target, name="TargetBot")
        match = Match(
            id="M_s1",
            name="Played Match",
            state=GameState.COMPLETED,
            scheduled_start=datetime(2026, 6, 4, tzinfo=timezone.utc),
            per_turn_deadline_seconds=60,
            game="hoard-hurt-help",
        )
        db.add(match)
        await db.flush()
        db.add(Player(match_id=match.id, user_id=target.id, agent_id=agent.id, seat_name="A"))
        await db.commit()
        admin_id, target_id = admin.id, target.id

    resp = await client.post(
        f"/admin/users/{target_id}/handle/reset", cookies=_cookies(admin_id)
    )
    assert resp.status_code == 303

    async with reset_db() as db:
        refreshed = (await db.execute(select(User).where(User.id == target_id))).scalar_one()
        players = (await db.execute(select(Player).where(Player.user_id == target_id))).scalars().all()
    assert refreshed.handle is None
    assert refreshed.handle_key is None  # the old name is freed for reuse
    assert len(players) == 1  # history preserved


async def test_admin_handles_page_lists_handles(reset_db, client):
    async with reset_db() as db:
        admin = User(
            google_sub="sub-admin",
            email="admin@test.com",
            handle="boss",
            handle_key="boss",
            role=UserRole.ADMIN,
        )
        db.add(admin)
        await db.commit()
        admin_id = admin.id

    resp = await client.get("/admin/handles", cookies=_cookies(admin_id))
    assert resp.status_code == 200
    assert "@boss" in resp.text


async def test_non_admin_cannot_reset_handle(reset_db, client):
    async with reset_db() as db:
        plain = await make_user(db, 6)
        victim = await make_user(db, 7)
        await db.commit()
        plain_id, victim_id = plain.id, victim.id

    resp = await client.post(
        f"/admin/users/{victim_id}/handle/reset",
        cookies=_cookies(plain_id),
        follow_redirects=False,
    )
    assert resp.status_code == 403
