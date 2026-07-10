"""Request tracing and incident lookup tests."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models import RequestIncident, User
from app.models.user import UserRole
from app.request_logging import install_request_logging, set_request_trace_context
from tests.conftest import signed_in_cookies as _cookies


@pytest.fixture(autouse=True)
def _autouse_admin_emails(admin_emails: None) -> None:
    """Every test in this file expects `admin@test.com` to already be an
    admin, matching this file's old autouse `reset_db` fixture.
    """


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


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
