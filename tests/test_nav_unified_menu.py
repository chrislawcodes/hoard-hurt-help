"""Unified nav menu: one menu per bar on a phone.

Signed in, the account pill (☰ + avatar) is the single menu and carries the
wayfinding links inside its panel; the standalone ☰ <details> gets the
`al-nav-menu-authed` class so CSS can drop it on phones. Signed out, the ☰
stays as the single menu and folds Sign in into its panel.
"""

import base64
import json

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner

from app.config import settings
from app.main import app
from app.models import Base
from tests.factories import make_user


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)

    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _signed_in_cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


def _acct_menu_html(page: str) -> str:
    """The account <details> markup (trigger + panel)."""
    start = page.index('<details class="al-acct"')
    return page[start : page.index("</details>", start)]


@pytest.mark.asyncio
async def test_signed_out_menu_folds_sign_in(client):
    r = await client.get("/games")
    assert r.status_code == 200
    # The ☰ menu is not flagged authed, so it stays the phone's single menu.
    assert 'class="al-nav-menu"' in r.text
    assert "al-nav-menu-authed" not in r.text
    # Sign in lives inside its panel (CSS reveals it on phones only).
    assert 'class="al-nav-menu-signin"' in r.text
    # No account pill when signed out.
    assert "al-acct-burger" not in r.text


@pytest.mark.asyncio
async def test_signed_in_pill_is_the_single_menu(client, reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        user_id = user.id

    r = await client.get("/games", cookies=_signed_in_cookies(user_id))
    assert r.status_code == 200
    # The standalone ☰ is flagged so CSS can hide it on phones…
    assert 'class="al-nav-menu al-nav-menu-authed"' in r.text
    # …because the account pill carries both halves of the unified menu.
    assert 'class="al-acct-burger"' in r.text
    acct = _acct_menu_html(r.text)
    assert 'class="al-acct-nav"' in acct
    assert 'href="/games"' in acct
    assert 'href="/leaderboard"' in acct
    # Signed in, the menu's signed-out Sign in item must not render.
    assert "al-nav-menu-signin" not in r.text


@pytest.mark.asyncio
async def test_marketing_nav_links_follow_into_pill_panel(client, reset_db):
    # The marketing home overrides nav_links (adds "How it works"); the pill's
    # panel renders the same block, so the override must follow along.
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        user_id = user.id

    r = await client.get("/", cookies=_signed_in_cookies(user_id))
    assert r.status_code == 200
    acct = _acct_menu_html(r.text)
    assert 'href="/#how"' in acct
