"""Regression tests for strategy-first agent onboarding.

These cover the slice that removes the connect-first gate from agent creation
and keeps the design form visible even when the user has no connections.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select

from app.config import PROVIDER_MODELS, settings
from app.main import app
from app.models import Base, Agent
from app.models.agent_version import AgentVersion
from app.models.connection import ConnectionProvider, ConnectionStatus
from app.routes.agents_health_presenter import _readiness_state
from tests.factories import make_agent, make_connection, make_user


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


def _signed_in_cookies(user_id: int) -> dict[str, str]:
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(
        json.dumps({"user_id": user_id, "next_after_login": None}).encode()
    ).decode()
    return {"hhh_session": signer.sign(payload).decode()}


@pytest.mark.asyncio
async def test_create_agent_without_connections_redirects_to_provider_connect(
    client, reset_db
) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()

    resp = await client.post(
        "/me/agents/new",
        cookies=_signed_in_cookies(user.id),
        data={
            "name": "Atlas",
            "model": "gpt-5.4-mini",
            "strategy_text": "Play to win.",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/me/connections?provider=openai"
    assert "/me/agents/" not in resp.headers["location"]

    async with reset_db() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.user_id == user.id, Agent.name == "Atlas"))
        ).scalar_one()
        version = (
            await db.execute(
                select(AgentVersion).where(AgentVersion.agent_id == agent.id)
            )
        ).scalar_one()

    assert agent.provider == ConnectionProvider.OPENAI
    assert agent.status.value == "active"
    assert version.model == "gpt-5.4-mini"
    assert version.strategy_text == "Play to win."

    follow = await client.get(resp.headers["location"], cookies=_signed_in_cookies(user.id))
    assert follow.status_code == 200
    assert 'id="byo-tab-codex"' in follow.text
    assert re.search(r'id="byo-tab-codex"[\s\S]*?class="byo-tab-input" checked', follow.text)
    assert 'hx-get="/me/connections/live-status?provider=openai"' in follow.text


@pytest.mark.asyncio
async def test_new_agent_form_renders_without_connections(client, reset_db) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()

    resp = await client.get("/me/agents/new", cookies=_signed_in_cookies(user.id))

    assert resp.status_code == 200
    assert "Connect an AI client first" not in resp.text
    assert 'name="model"' in resp.text
    assert 'name="strategy_text"' in resp.text

    for provider_value, models in PROVIDER_MODELS.items():
        if not models:
            continue
        label = "OpenAI" if provider_value == "openai" else provider_value.capitalize()
        assert f'<optgroup label="{label}">' in resp.text
        assert f'<optgroup label="{label}" disabled>' not in resp.text
        for model in models:
            assert f'<option value="{model}"' in resp.text


@pytest.mark.asyncio
async def test_create_agent_without_connections_preserves_next_in_connect_redirect(
    client, reset_db
) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()

    next_url = "/games/hoard-hurt-help/matches/G_001/join"
    resp = await client.post(
        "/me/agents/new",
        cookies=_signed_in_cookies(user.id),
        data={
            "name": "Atlas",
            "model": "gpt-5.4-mini",
            "strategy_text": "Play to win.",
            "next": next_url,
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/me/connections?provider=openai&next={quote(next_url, safe='')}"


@pytest.mark.asyncio
async def test_connections_page_with_provider_hint_keeps_other_live_connections_from_redirecting(
    client, reset_db
) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        # A live Claude connection exists, but we're routing the user to connect
        # OpenAI next. The page must stay put so the OpenAI tab can be used.
        connection, _ = await make_connection(db, user)
        connection.last_seen_at = datetime.now(timezone.utc)
        await db.commit()

    resp = await client.get(
        "/me/connections?provider=openai&next=/games/hoard-hurt-help/matches/G_001/join",
        cookies=_signed_in_cookies(user.id),
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert re.search(r'id="byo-tab-codex"[\s\S]*?class="byo-tab-input" checked', resp.text)
    assert "/games/hoard-hurt-help/matches/G_001/join" in resp.text


def test_readiness_state_marks_paused_connections_as_needs_connecting() -> None:
    from app.engine.connection_health import ConnectionHealth, ConnectionHealthStatus

    paused_health = ConnectionHealthStatus(
        state=ConnectionHealth.PAUSED,
        label="Paused",
        badge_class="badge-done",
        pulse=False,
        needs_reconnect=False,
        never_connected=False,
        last_connected_at=None,
        last_connected_human=None,
    )
    assert _readiness_state({"health": paused_health, "join_blocked": False}) == "paused"

    disconnected_health = ConnectionHealthStatus(
        state=ConnectionHealth.DISCONNECTED,
        label="Needs connecting",
        badge_class="badge-alert",
        pulse=False,
        needs_reconnect=True,
        never_connected=True,
        last_connected_at=None,
        last_connected_human=None,
    )
    assert _readiness_state({"health": disconnected_health, "join_blocked": False}) == "needs_connecting"


@pytest.mark.asyncio
async def test_agent_list_marks_paused_only_provider_as_needs_connecting(
    client, reset_db
) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(
            db, user, status=ConnectionStatus.PAUSED
        )
        agent, _ = await make_agent(db, user, connection=connection, name="Atlas")
        await db.commit()

    resp = await client.get("/me/agents", cookies=_signed_in_cookies(user.id))

    assert resp.status_code == 200
    assert "Needs connecting" in resp.text
    assert 'Connect Claude →' in resp.text
    assert 'href="/me/connections?provider=claude"' in resp.text


@pytest.mark.asyncio
async def test_agent_detail_marks_paused_only_provider_as_needs_connecting(
    client, reset_db
) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        connection, _ = await make_connection(
            db, user, status=ConnectionStatus.PAUSED
        )
        agent, _ = await make_agent(db, user, connection=connection, name="Atlas")
        await db.commit()

    resp = await client.get(
        f"/me/agents/{agent.id}", cookies=_signed_in_cookies(user.id)
    )

    assert resp.status_code == 200
    assert "Needs connecting" in resp.text
    assert "No live connection runs Claude" in resp.text
    assert 'href="/me/connections?provider=claude"' in resp.text


@pytest.mark.asyncio
async def test_new_agent_form_offers_existing_strategies(client, reset_db) -> None:
    """The create form exposes the user's existing agents' strategies so they can
    start a new agent from one instead of retyping it (PR 1: reuse picker)."""
    async with reset_db() as db:
        user = await make_user(db)
        await make_agent(
            db,
            user,
            name="Veteran",
            strategy_text="Cooperate first, then mirror.",
        )
        await db.commit()

    resp = await client.get("/me/agents/new", cookies=_signed_in_cookies(user.id))

    assert resp.status_code == 200
    # The existing agent is offered as a "start from" option...
    assert 'id="existing-strategy"' in resp.text
    assert "Veteran" in resp.text
    # ...and its strategy text is available for the client-side fill.
    assert "Cooperate first, then mirror." in resp.text


@pytest.mark.asyncio
async def test_new_agent_form_hides_reuse_picker_without_existing_agents(
    client, reset_db
) -> None:
    """With no existing agents, the "start from existing" picker is not rendered."""
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()

    resp = await client.get("/me/agents/new", cookies=_signed_in_cookies(user.id))

    assert resp.status_code == 200
    assert 'id="existing-strategy"' not in resp.text
