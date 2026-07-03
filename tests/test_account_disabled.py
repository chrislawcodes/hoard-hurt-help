"""Tests for account-disabled enforcement (Slice 2)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.engine.tokens import bot_key_lookup
from app.models import ConnectionSetup, User
from app.models.user import UserRole
from app.routes.auth import sync_google_user
from app.schemas.auth import GoogleUserInfo
from tests.factories import make_connection, make_user


def _signed_in(user_id: int) -> dict[str, str]:
    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _seed_disabled_user(reset_db: async_sessionmaker) -> User:
    async with reset_db() as db:
        user = await make_user(db)
        user.disabled_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)
        return user


@pytest.mark.asyncio
async def test_disabled_user_html_redirect(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    """Disabled user on an HTML page gets 303 to /disabled."""
    user = await _seed_disabled_user(reset_db)
    resp = await client.get(
        "/me/agents",
        cookies=_signed_in(user.id),
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/disabled"


@pytest.mark.asyncio
async def test_disabled_user_htmx_redirect(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    """Disabled user on HTMX request gets 200 + HX-Redirect header."""
    user = await _seed_disabled_user(reset_db)
    resp = await client.get(
        "/me/agents",
        cookies=_signed_in(user.id),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert resp.headers["hx-redirect"] == "/disabled"


@pytest.mark.asyncio
async def test_disabled_page_loads_no_loop(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    """/disabled is accessible to a disabled (signed-in) user — no auth loop."""
    user = await _seed_disabled_user(reset_db)
    resp = await client.get(
        "/disabled",
        cookies=_signed_in(user.id),
        follow_redirects=False,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_connection_key_disabled_user_blocked(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    """A connection key for a disabled user is rejected with 403 ACCOUNT_DISABLED."""
    async with reset_db() as db:
        user = await make_user(db)
        user.disabled_at = datetime.now(timezone.utc)
        _, plain_key = await make_connection(db, user)
        await db.commit()
    resp = await client.get(
        "/api/agent/next-turn",
        headers={"X-Connection-Key": plain_key},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"]["code"] == "ACCOUNT_DISABLED"


@pytest.mark.asyncio
async def test_connection_setup_for_disabled_user_is_blocked(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    """A first-use connection setup key for a disabled user is rejected."""
    plain_key = "sk_conn_setup_disabled_1"
    async with reset_db() as db:
        user = await make_user(db)
        user.disabled_at = datetime.now(timezone.utc)
        setup = ConnectionSetup(
            user_id=user.id,
            provider=None,
            nickname="Machine",
            key_lookup=bot_key_lookup(plain_key),
            key_hint=plain_key[-4:],
        )
        db.add(setup)
        await db.commit()
    resp = await client.get(
        "/api/agent/next-turn",
        headers={"X-Connection-Key": plain_key},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"]["code"] == "ACCOUNT_DISABLED"


@pytest.mark.asyncio
async def test_promoted_user_keeps_admin_role_after_login(
    reset_db: async_sessionmaker,
) -> None:
    """An in-app-promoted ADMIN keeps their role across Google re-logins."""
    async with reset_db() as db:
        user = await make_user(db)
        user.role = UserRole.ADMIN
        await db.commit()
        await db.refresh(user)
        stored_id = user.id
        stored_email = user.email
        stored_sub = user.google_sub

    async with reset_db() as db:
        userinfo = GoogleUserInfo(
            sub=stored_sub,
            email=stored_email,
            name="Test",
            given_name="Test",
            family_name="User",
        )
        result = await sync_google_user(db, userinfo)
        await db.commit()
        await db.refresh(result)

    assert result.id == stored_id
    assert result.role == UserRole.ADMIN


@pytest.mark.asyncio
async def test_config_email_always_gets_admin(
    reset_db: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config-floor email is always elevated to ADMIN on login."""
    test_email = "floor@example.com"
    monkeypatch.setattr(settings, "platform_admin_emails", test_email)

    async with reset_db() as db:
        userinfo = GoogleUserInfo(
            sub="sub-floor",
            email=test_email,
            name="Floor",
            given_name="Floor",
            family_name="Admin",
        )
        result = await sync_google_user(db, userinfo)
        await db.commit()
        await db.refresh(result)

    assert result.role == UserRole.ADMIN
