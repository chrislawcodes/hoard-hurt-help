"""The agent setup screen leads with the runner, and the runner script is served."""

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


@pytest.mark.asyncio
async def test_agent_runner_scripts_are_served() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for name in (
            "agentludum_agent.py",
            "agentludum_agent_codex.py",
            "agentludum_agent_gemini.py",
        ):
            r = await c.get(f"/runners/{name}")
            assert r.status_code == 200, name
            # It's the real runner file, not an HTML page.
            assert "/api/agent/next-turn" in r.text, name
        # Anything not on the allowlist is a 404 — no path-traversal surface.
        bad = await c.get("/runners/secrets.py")
        assert bad.status_code == 404


@pytest.mark.asyncio
async def test_setup_screen_leads_with_runner(reset_db) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        uid = user.id

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"hhh_session": _cookie(uid)},
        follow_redirects=True,
    ) as c:
        # Creating a connection lands on the setup page with the one-time runner message.
        r = await c.post("/me/connections", data={"provider": "claude", "nickname": "Atlas"})
    assert r.status_code == 200, r.text
    body = r.text
    # The chained-session setup is the primary path; the page now hands back
    # the connection runner, not the removed bot runner.
    assert "curl -fsSL" in body
    assert "/runners/agentludum_connector.py" in body
    assert "This connection uses the Claude login I already have." in body
    assert "X-Connection-Key" in body
    # Tells the operator how to stop the agent.
    assert "Keep one session per match" in body
    assert "Setup AI Provider Connection" in body
