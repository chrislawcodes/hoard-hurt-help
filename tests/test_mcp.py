"""MCP server smoke tests and OAuth bridge checks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.main import app
from app.models.base import Base
from fastmcp.server.dependencies import AccessToken


def _token(*, sub: str = "sub-123", email: str = "agent@example.com") -> AccessToken:
    return AccessToken(
        token="access-token-1",
        client_id=sub,
        scopes=["openid", "email", "profile"],
        subject=sub,
        claims={
            "sub": sub,
            "email": email,
            "name": "Agent One",
            "given_name": "Agent",
            "family_name": "One",
            "email_verified": True,
        },
    )


@pytest.fixture
async def db_session_factory(
    engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_mcp_tools_registered() -> None:
    """The MCP tool set still includes the public game and play actions."""
    from mcp_server.server import mcp_app

    tool_names = {tool.name for tool in await mcp_app.list_tools()}
    assert {
        "get_turn",
        "get_next_turn",
        "submit_talk",
        "submit_action",
        "get_game_state",
        "get_opponent_history",
        "get_chat",
        "get_turn_detail",
        "get_standings",
    }.issubset(tool_names)


@pytest.mark.asyncio
async def test_authed_tools_hide_token_and_db_from_schema() -> None:
    """OAuth plumbing stays hidden from the LLM-visible tool schema."""
    from mcp_server.server import mcp_app

    schemas = {
        t.name: (t.parameters or {}).get("properties", {})
        for t in await mcp_app.list_tools()
    }
    for name in (
        "get_turn",
        "get_next_turn",
        "submit_talk",
        "submit_action",
        "get_game_state",
        "get_opponent_history",
        "get_chat",
        "get_turn_detail",
        "get_standings",
    ):
        assert "token" not in schemas[name]
        assert "db" not in schemas[name]


@pytest.mark.asyncio
async def test_mcp_discovery_requires_bearer_token() -> None:
    """The MCP endpoint advertises OAuth discovery instead of a secret header."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/mcp")
        assert response.status_code == 401
        challenge = response.headers["www-authenticate"]
        assert "Bearer" in challenge
        assert "/.well-known/oauth-protected-resource/mcp" in challenge

        prm = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert prm.status_code == 200
        assert prm.json()["authorization_servers"]

        as_metadata = await client.get("/.well-known/oauth-authorization-server")
        assert as_metadata.status_code == 200
        assert as_metadata.json()["authorization_endpoint"].endswith("/authorize")
        assert as_metadata.json()["token_endpoint"].endswith("/token")
        assert as_metadata.json()["registration_endpoint"].endswith("/register")


@pytest.mark.asyncio
async def test_get_next_turn_uses_google_identity_and_mode_a_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bridge resolves the Google identity and builds the Mode A connection."""
    from mcp_server import server

    captured: dict[str, object] = {}

    async def fake_sync_google_user(db: object, userinfo: object) -> SimpleNamespace:
        captured["userinfo"] = userinfo
        return SimpleNamespace(id=42, google_sub=userinfo.sub, disabled_at=None)

    async def fake_mode_a_connection_for(
        db: object, user: object, *, provider: object = None
    ) -> SimpleNamespace:
        captured["user"] = user
        return SimpleNamespace(id=7, key_lookup="lookup-7", user=user)

    def fake_assert_connection_usable(connection: object) -> None:
        captured["checked_connection"] = connection

    async def fake_mark_seen(db: object, connection: object, *, key_hash: str) -> None:
        captured["mark_seen_key_hash"] = key_hash
        captured["mark_seen_connection"] = connection

    async def fake_get_next_turn(
        db: object,
        connection: object,
        *,
        hold_seconds: float,
        interval_seconds: float,
    ) -> dict[str, object]:
        captured["service_connection"] = connection
        captured["hold_seconds"] = hold_seconds
        captured["interval_seconds"] = interval_seconds
        return {"status": "waiting", "next_poll_after_seconds": 2}

    monkeypatch.setattr(server, "sync_google_user", fake_sync_google_user)
    monkeypatch.setattr(server, "mode_a_connection_for", fake_mode_a_connection_for)
    monkeypatch.setattr(server, "assert_connection_usable", fake_assert_connection_usable)
    monkeypatch.setattr(server, "mark_seen", fake_mark_seen)
    monkeypatch.setattr(server, "play_get_next_turn", fake_get_next_turn)

    result = await server.get_next_turn(token=_token(), db=object())

    assert result["status"] == "waiting"
    assert captured["userinfo"].sub == "sub-123"
    assert captured["userinfo"].email == "agent@example.com"
    assert captured["user"].google_sub == "sub-123"
    assert captured["checked_connection"].key_lookup == "lookup-7"
    assert captured["mark_seen_key_hash"] == "lookup-7"
    assert captured["service_connection"].id == 7


@pytest.mark.asyncio
async def test_get_turn_uses_oauth_player_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Match-scoped tools resolve the signed-in user's player, not a header key."""
    from mcp_server import server

    captured: dict[str, object] = {}

    async def fake_sync_google_user(db: object, userinfo: object) -> SimpleNamespace:
        captured["userinfo"] = userinfo
        return SimpleNamespace(id=42, google_sub=userinfo.sub, disabled_at=None)

    async def fake_mode_a_connection_for(
        db: object, user: object, *, provider: object = None
    ) -> SimpleNamespace:
        captured["user"] = user
        return SimpleNamespace(id=7, key_lookup="lookup-7", user=user)

    def fake_assert_connection_usable(connection: object) -> None:
        captured["checked_connection"] = connection

    async def fake_mark_seen(db: object, connection: object, *, key_hash: str) -> None:
        captured["mark_seen_key_hash"] = key_hash

    async def fake_require_agent_player(
        *,
        match_id: str,
        db: object,
        connection: object,
        agent_id: int | None = None,
        agent_turn_token: str | None = None,
    ) -> SimpleNamespace:
        captured["resolved_match_id"] = match_id
        captured["resolved_connection"] = connection
        captured["resolved_agent_turn_token"] = agent_turn_token
        return SimpleNamespace(id=99, agent_id=17, seat_name="AI-17")

    async def fake_poll_turn(
        db: object,
        *,
        match_id: str,
        player: object,
        rate_state: dict[int, float],
    ) -> dict[str, object]:
        captured["poll_match_id"] = match_id
        captured["poll_player"] = player
        captured["poll_rate_state"] = rate_state
        return {"status": "your_turn", "current": {"phase": "talk"}}

    monkeypatch.setattr(server, "sync_google_user", fake_sync_google_user)
    monkeypatch.setattr(server, "mode_a_connection_for", fake_mode_a_connection_for)
    monkeypatch.setattr(server, "assert_connection_usable", fake_assert_connection_usable)
    monkeypatch.setattr(server, "mark_seen", fake_mark_seen)
    monkeypatch.setattr(server, "require_agent_player", fake_require_agent_player)
    monkeypatch.setattr(server, "poll_turn", fake_poll_turn)

    result = await server.get_turn(match_id="M_001", token=_token(), db=object())

    assert result["status"] == "your_turn"
    assert captured["userinfo"].sub == "sub-123"
    assert captured["resolved_match_id"] == "M_001"
    assert captured["resolved_connection"].id == 7
    assert captured["resolved_agent_turn_token"] is None
    assert captured["poll_match_id"] == "M_001"
    assert captured["poll_player"].seat_name == "AI-17"
    assert isinstance(captured["poll_rate_state"], dict)


@pytest.mark.asyncio
async def test_get_game_state_requires_auth() -> None:
    """Anonymous MCP calls are rejected before any game state is read."""
    from mcp_server import server

    with pytest.raises(RuntimeError, match="verified access token"):
        await server.get_game_state(match_id="M_001", db=object())


def test_mcp_asgi_app_constructed() -> None:
    """The MCP HTTP app is built and importable."""
    from mcp_server.server import asgi_app

    assert asgi_app is not None


def test_mcp_root_mount_and_public_mcp_route() -> None:
    """The FastAPI app mounts the MCP app at the root, and the MCP route exists."""
    from app.main import app as fastapi_app
    from mcp_server.server import asgi_app

    # Use the public url_path_for() rather than scanning app.routes for a flat
    # "/" entry: FastAPI >=0.137 registers sub-routers lazily (_IncludedRouter),
    # so the home route is not a flat list entry until the app is built.
    # url_path_for() resolves through lazy includes and works on both behaviours.
    assert fastapi_app.url_path_for("home") == "/"
    assert any(getattr(route, "path", None) == "/mcp" for route in asgi_app.routes)


@pytest.mark.asyncio
async def test_pull_tools_use_shared_oauth_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pull tools all resolve through the OAuth identity path."""
    from mcp_server import server

    captured: dict[str, object] = {}

    async def fake_sync_google_user(db: object, userinfo: object) -> SimpleNamespace:
        captured["userinfo"] = userinfo
        return SimpleNamespace(id=42, google_sub=userinfo.sub, disabled_at=None)

    async def fake_mode_a_connection_for(
        db: object, user: object, *, provider: object = None
    ) -> SimpleNamespace:
        return SimpleNamespace(id=7, key_lookup="lookup-7", user=user)

    def fake_assert_connection_usable(connection: object) -> None:
        pass

    async def fake_mark_seen(db: object, connection: object, *, key_hash: str) -> None:
        pass

    async def fake_require_agent_player(
        *,
        match_id: str,
        db: object,
        connection: object,
        agent_id: int | None = None,
        agent_turn_token: str | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(id=99, agent_id=17, seat_name="AI-17")

    async def fake_pull(
        db: object,
        *,
        match_id: str,
        player: object,
        rate_state: dict[tuple[int, str], float],
        **kwargs: object,
    ) -> dict[str, object]:
        captured["match_id"] = match_id
        captured["player"] = player
        captured["kwargs"] = kwargs
        captured["rate_state"] = rate_state
        return {"status": "ok"}

    monkeypatch.setattr(server, "sync_google_user", fake_sync_google_user)
    monkeypatch.setattr(server, "mode_a_connection_for", fake_mode_a_connection_for)
    monkeypatch.setattr(server, "assert_connection_usable", fake_assert_connection_usable)
    monkeypatch.setattr(server, "mark_seen", fake_mark_seen)
    monkeypatch.setattr(server, "require_agent_player", fake_require_agent_player)
    monkeypatch.setattr(server, "opponent_history", fake_pull)
    monkeypatch.setattr(server, "chat_transcript", fake_pull)
    monkeypatch.setattr(server, "turn_detail", fake_pull)
    monkeypatch.setattr(server, "standings", fake_pull)

    token = _token()
    assert await server.get_opponent_history(
        match_id="M_001",
        opponent_id="AI-2",
        token=token,
        db=object(),
    ) == {"status": "ok"}
    assert await server.get_chat(match_id="M_001", token=token, db=object()) == {
        "status": "ok"
    }
    assert await server.get_turn_detail(
        match_id="M_001",
        round=1,
        turn=1,
        token=token,
        db=object(),
    ) == {"status": "ok"}
    assert await server.get_standings(match_id="M_001", token=token, db=object()) == {
        "status": "ok"
    }
