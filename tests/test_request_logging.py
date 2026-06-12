"""Request tracing and incident lookup tests."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import Base, RequestIncident, User
from app.models.user import UserRole
from app.request_logging import install_request_logging, set_request_trace_context


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
    monkeypatch.setattr(settings, "admin_emails", "admin@test.com")

    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _cookies(user_id: int) -> dict[str, str]:
    from itsdangerous import TimestampSigner
    import base64
    import json

    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(json.dumps({"user_id": user_id}).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


@pytest.mark.asyncio
async def test_request_logging_persists_incident_and_request_id(reset_db):
    trace_app = FastAPI()
    install_request_logging(trace_app)

    @trace_app.get("/boom")
    async def boom(request: Request) -> None:
        set_request_trace_context(
            request,
            match_id="G_999",
            stage="join_submit",
            bot_id=7,
            player_id=8,
        )
        raise RuntimeError("kaboom")

    transport = ASGITransport(app=trace_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    request_id = response.headers["x-request-id"]
    assert request_id in response.text

    async with reset_db() as db:
        incident = (
            await db.execute(
                select(RequestIncident).where(RequestIncident.request_id == request_id)
            )
        ).scalar_one()
    assert incident.path == "/boom"
    assert incident.method == "GET"
    assert incident.match_id == "G_999"
    assert incident.stage == "join_submit"
    assert incident.bot_id == 7
    assert incident.player_id == 8
    assert incident.error_type == "RuntimeError"
    assert "kaboom" in incident.error_message
    assert "kaboom" in incident.stacktrace
    assert '"match_id": "G_999"' in (incident.context_json or "")


@pytest.mark.asyncio
async def test_admin_incidents_page_lists_seeded_incident(client, reset_db):
    async with reset_db() as db:
        admin = User(
            google_sub="sub-admin",
            email="admin@test.com",
            name="Admin",
            role=UserRole.ADMIN,
        )
        db.add(admin)
        await db.flush()
        db.add(
            RequestIncident(
                request_id="abc12345",
                method="GET",
                path="/games/G_001/join",
                query_string="",
                user_id=admin.id,
                match_id="G_001",
                bot_id=None,
                player_id=None,
                stage="join_form",
                error_type="RuntimeError",
                error_message="boom",
                stacktrace="trace",
                context_json='{"match_id": "G_001"}',
            )
        )
        await db.commit()

    r = await client.get("/admin/incidents", cookies=_cookies(admin.id))
    assert r.status_code == 200
    assert "abc12345" in r.text
    assert "join_form" in r.text

    r2 = await client.get(
        "/admin/incidents",
        params={"request_id": "abc12345"},
        cookies=_cookies(admin.id),
    )
    assert r2.status_code == 200
    assert "abc12345" in r2.text
    assert "boom" in r2.text
