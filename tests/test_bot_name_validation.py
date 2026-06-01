"""Bot names may contain spaces and run up to 120 characters.

The friendly bot label on the /me/bots page is separate from the in-game agent
id (a stricter 32-char, no-space field set at game entry). This pins the bot
label's rules so a future tweak to the in-game validator can't quietly tighten
them.
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


def _cookie(user_id: int) -> str:
    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return signer.sign(payload).decode()


async def _authed_client(reset_db) -> tuple[AsyncClient, int]:
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        uid = user.id
    transport = ASGITransport(app=app)
    client = AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"hhh_session": _cookie(uid)},
        follow_redirects=True,
    )
    return client, uid


@pytest.mark.asyncio
async def test_long_name_with_spaces_is_accepted(reset_db) -> None:
    # Spaces, mixed case, right at the 120-char ceiling.
    name = ("Strategic Tit For Tat " * 6).strip()  # 137 -> trimmed below
    name = name[:120].strip()
    assert " " in name and 100 < len(name) <= 120

    client, _ = await _authed_client(reset_db)
    async with client as c:
        r = await c.post("/me/bots", data={"name": name})

    # Lands on the new bot's detail page (200 after the redirect is followed),
    # and the page shows the name we asked for.
    assert r.status_code == 200, r.text
    assert name in r.text


@pytest.mark.asyncio
async def test_name_over_120_chars_is_rejected(reset_db) -> None:
    name = "x" * 121

    client, _ = await _authed_client(reset_db)
    async with client as c:
        r = await c.post("/me/bots", data={"name": name})

    assert r.status_code == 400
    assert "1–120" in r.text


@pytest.mark.asyncio
async def test_rename_to_long_spaced_name_is_accepted(reset_db) -> None:
    client, _ = await _authed_client(reset_db)
    async with client as c:
        created = await c.post("/me/bots", data={"name": "Atlas"})
        assert created.status_code == 200, created.text
        # The detail URL carries the new bot's id; rename through it.
        bot_id = created.url.path.rsplit("/", 1)[-1]
        new_name = "Atlas The Diplomatic Cooperator Bot"
        renamed = await c.post(f"/me/bots/{bot_id}/rename", data={"name": new_name})

    assert renamed.status_code == 200, renamed.text
    assert new_name in renamed.text
