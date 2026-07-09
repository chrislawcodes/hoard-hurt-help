"""The dev-only login: it works in local dev, and it is impossible to reach in
production (off by default, and hard-disabled whenever secure cookies are on)."""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.main import create_app
from app.models.user import User
from app.routes.dev_login import dev_login_available
from tests.factories import make_user


def _paths(app: FastAPI) -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def _build_app(monkeypatch, *, enabled: bool, secure: bool) -> FastAPI:
    monkeypatch.setattr(settings, "dev_login_enabled", enabled)
    monkeypatch.setattr(settings, "cookie_secure", secure)
    return create_app()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def test_dev_login_available_only_when_enabled_and_not_secure(monkeypatch):
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "dev_login_enabled", True)
    assert dev_login_available() is True

    monkeypatch.setattr(settings, "dev_login_enabled", False)
    assert dev_login_available() is False

    # The prod lockout: even enabled, secure cookies (how prod runs) disable it.
    monkeypatch.setattr(settings, "dev_login_enabled", True)
    monkeypatch.setattr(settings, "cookie_secure", True)
    assert dev_login_available() is False


def test_route_absent_when_disabled_or_in_prod_shape(monkeypatch):
    assert "/dev/login" not in _paths(_build_app(monkeypatch, enabled=False, secure=False))
    # Enabled but secure cookies (prod) → still not mounted.
    assert "/dev/login" not in _paths(_build_app(monkeypatch, enabled=True, secure=True))
    # Local dev → mounted.
    assert "/dev/login" in _paths(_build_app(monkeypatch, enabled=True, secure=False))


async def test_dev_login_signs_in_and_seeds_the_dev_user(monkeypatch, reset_db):
    app = _build_app(monkeypatch, enabled=True, secure=False)
    async with _client(app) as c:
        r = await c.get("/dev/login", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/me/agents"
        set_cookie = " ".join(r.headers.get_list("set-cookie"))
        assert "hhh_session=" in set_cookie  # a real session was issued

        # The session cookie is now in the jar — a gated page loads signed in.
        page = await c.get("/me/agents")
        assert page.status_code == 200

    async with reset_db() as db:
        dev = (
            await db.execute(select(User).where(User.email == "dev@localhost"))
        ).scalar_one_or_none()
        assert dev is not None
        assert dev.handle == "dev"


async def test_dev_login_next_must_be_same_site(monkeypatch, reset_db):
    app = _build_app(monkeypatch, enabled=True, secure=False)
    async with _client(app) as c:
        off_site = await c.get("/dev/login?next=https://evil.example/x", follow_redirects=False)
        assert off_site.headers["location"] == "/me/agents"  # off-site target rejected

        same_site = await c.get("/dev/login?next=/me/connections", follow_redirects=False)
        assert same_site.headers["location"] == "/me/connections"


async def test_dev_login_can_target_a_specific_user(monkeypatch, reset_db):
    async with reset_db() as db:
        user = await make_user(db, i=7)
        await db.commit()
        target_id = user.id

    app = _build_app(monkeypatch, enabled=True, secure=False)
    async with _client(app) as c:
        ok = await c.get(f"/dev/login?user_id={target_id}", follow_redirects=False)
        assert ok.status_code == 303

        missing = await c.get("/dev/login?user_id=999999", follow_redirects=False)
        assert missing.status_code == 404
