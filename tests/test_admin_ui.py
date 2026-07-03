"""Tests for admin user management UI (Slice 4)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.models import GameState, Match, Player
from app.models.user import User, UserRole
from tests.factories import make_agent, make_connection, make_user


def _signed_in(user_id: int) -> dict[str, str]:
    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _admin_user(reset_db: async_sessionmaker) -> User:
    async with reset_db() as db:
        u = await make_user(db, 0)
        u.role = UserRole.ADMIN
        await db.commit()
        await db.refresh(u)
        return u


async def _regular_user(reset_db: async_sessionmaker, i: int = 1) -> User:
    async with reset_db() as db:
        u = await make_user(db, i)
        await db.commit()
        await db.refresh(u)
        return u


@pytest.mark.asyncio
async def test_users_list_renders(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    admin = await _admin_user(reset_db)
    resp = await client.get(
        "/admin/users",
        cookies=_signed_in(admin.id),
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b"Users" in resp.content


@pytest.mark.asyncio
async def test_users_list_q_filter_email(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    admin = await _admin_user(reset_db)
    other = await _regular_user(reset_db, 1)
    resp = await client.get(
        f"/admin/users?q={other.email}",
        cookies=_signed_in(admin.id),
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert other.email.encode() in resp.content


@pytest.mark.asyncio
async def test_users_list_q_filter_handle(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    admin = await _admin_user(reset_db)
    other = await _regular_user(reset_db, 1)
    resp = await client.get(
        f"/admin/users?q={other.handle}",
        cookies=_signed_in(admin.id),
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert other.handle.encode() in resp.content


@pytest.mark.asyncio
async def test_user_detail_renders(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    admin = await _admin_user(reset_db)
    other = await _regular_user(reset_db, 1)
    resp = await client.get(
        f"/admin/users/{other.id}",
        cookies=_signed_in(admin.id),
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert other.email.encode() in resp.content


@pytest.mark.asyncio
async def test_user_detail_shows_audit(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    admin = await _admin_user(reset_db)
    other = await _regular_user(reset_db, 1)
    await client.post(
        f"/admin/users/{other.id}/disable",
        cookies=_signed_in(admin.id),
        follow_redirects=False,
    )
    resp = await client.get(
        f"/admin/users/{other.id}",
        cookies=_signed_in(admin.id),
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b"disable" in resp.content


@pytest.mark.asyncio
async def test_user_detail_shows_counts_and_recent_match(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    admin = await _admin_user(reset_db)
    async with reset_db() as db:
        target = await make_user(db, 2)
        await make_connection(db, target)
        agent, _ = await make_agent(db, target)
        match = Match(
            id="M_ui_1",
            name="Admin UI Match",
            game="hoard-hurt-help",
            state=GameState.COMPLETED,
            scheduled_start=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        db.add(match)
        await db.flush()
        db.add(
            Player(
                match_id=match.id,
                user_id=target.id,
                agent_id=agent.id,
                seat_name="AI-1",
            )
        )
        await db.commit()
    resp = await client.get(
        f"/admin/users/{target.id}",
        cookies=_signed_in(admin.id),
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert b"Connections" in resp.content
    assert b"Agents" in resp.content
    assert b"Admin UI Match" in resp.content


@pytest.mark.asyncio
async def test_non_admin_cannot_view_list(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    other = await _regular_user(reset_db, 0)
    resp = await client.get(
        "/admin/users",
        cookies=_signed_in(other.id),
        follow_redirects=False,
    )
    assert resp.status_code == 403
