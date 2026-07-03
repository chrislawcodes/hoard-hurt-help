"""Tests for admin user action service (Slice 3)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.models import AdminAuditLog
from app.models.user import UserRole
from app.services.admin_user_actions import (
    demote_user,
    disable_user,
    enable_user,
    promote_user,
    reset_handle,
)
from tests.factories import make_user


def _signed_in(user_id: int) -> dict[str, str]:
    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


@pytest.mark.asyncio
async def test_disable_writes_audit_row(reset_db: async_sessionmaker) -> None:
    async with reset_db() as db:
        actor = await make_user(db, 0)
        target = await make_user(db, 1)
        await disable_user(db, actor=actor, target_id=target.id)
        await db.commit()
        logs = (await db.execute(select(AdminAuditLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].action.value == "disable"
    assert logs[0].actor_user_id == actor.id
    assert logs[0].target_user_id == target.id


@pytest.mark.asyncio
async def test_disable_noop_no_audit_row(reset_db: async_sessionmaker) -> None:
    async with reset_db() as db:
        actor = await make_user(db, 0)
        target = await make_user(db, 1)
        target.disabled_at = datetime.now(timezone.utc)
        await db.flush()
        await disable_user(db, actor=actor, target_id=target.id)
        await db.commit()
        logs = (await db.execute(select(AdminAuditLog))).scalars().all()
    assert len(logs) == 0


@pytest.mark.asyncio
async def test_enable_writes_audit_row(reset_db: async_sessionmaker) -> None:
    async with reset_db() as db:
        actor = await make_user(db, 0)
        target = await make_user(db, 1)
        target.disabled_at = datetime.now(timezone.utc)
        await db.flush()
        await enable_user(db, actor=actor, target_id=target.id)
        await db.commit()
        logs = (await db.execute(select(AdminAuditLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].action.value == "enable"


@pytest.mark.asyncio
async def test_promote_writes_audit_row(reset_db: async_sessionmaker) -> None:
    async with reset_db() as db:
        actor = await make_user(db, 0)
        target = await make_user(db, 1)
        await promote_user(db, actor=actor, target_id=target.id)
        await db.commit()
        logs = (await db.execute(select(AdminAuditLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].action.value == "promote"


@pytest.mark.asyncio
async def test_demote_writes_audit_row(reset_db: async_sessionmaker) -> None:
    async with reset_db() as db:
        actor = await make_user(db, 0)
        target = await make_user(db, 1)
        target.role = UserRole.ADMIN
        await db.flush()
        await demote_user(db, actor=actor, target_id=target.id)
        await db.commit()
        logs = (await db.execute(select(AdminAuditLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].action.value == "demote"


@pytest.mark.asyncio
async def test_floor_admin_refuses_demote(
    reset_db: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    floor_email = "floor@example.com"
    monkeypatch.setattr(settings, "platform_admin_emails", floor_email)
    async with reset_db() as db:
        actor = await make_user(db, 0)
        target = await make_user(db, 1)
        target.email = floor_email
        target.role = UserRole.ADMIN
        await db.flush()
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await demote_user(db, actor=actor, target_id=target.id)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_floor_admin_refuses_disable(
    reset_db: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    floor_email = "Floor@Example.COM"  # test case-insensitivity
    monkeypatch.setattr(settings, "platform_admin_emails", floor_email.lower())
    async with reset_db() as db:
        actor = await make_user(db, 0)
        target = await make_user(db, 1)
        target.email = floor_email
        await db.flush()
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await disable_user(db, actor=actor, target_id=target.id)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_handle_reset_writes_audit_row(reset_db: async_sessionmaker) -> None:
    async with reset_db() as db:
        actor = await make_user(db, 0)
        target = await make_user(db, 1)
        await reset_handle(db, actor=actor, target_id=target.id)
        await db.commit()
        logs = (await db.execute(select(AdminAuditLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].action.value == "handle_reset"


@pytest.mark.asyncio
async def test_handle_reset_no_handle_is_noop(reset_db: async_sessionmaker) -> None:
    async with reset_db() as db:
        actor = await make_user(db, 0)
        target = await make_user(db, 1)
        target.handle = None
        target.handle_key = None
        await db.flush()
        await reset_handle(db, actor=actor, target_id=target.id)
        await db.commit()
        logs = (await db.execute(select(AdminAuditLog))).scalars().all()
    assert len(logs) == 0


@pytest.mark.asyncio
async def test_non_admin_cannot_disable(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    async with reset_db() as db:
        actor = await make_user(db, 0)
        target = await make_user(db, 1)
        await db.commit()
    resp = await client.post(
        f"/admin/users/{target.id}/disable",
        cookies=_signed_in(actor.id),
        follow_redirects=False,
    )
    assert resp.status_code == 403
