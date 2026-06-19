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
    """The MCP tool set matches the cleaned 7-tool surface."""
    from mcp_server.server import mcp_app

    tool_names = {tool.name for tool in await mcp_app.list_tools()}
    assert tool_names == {
        "get_instructions",
        "get_next_turn",
        "get_next_turns",
        "submit_talk",
        "submit_action",
        "get_chat",
        "get_game_state",
    }


@pytest.mark.asyncio
async def test_get_next_turn_exposes_agent_id_for_parallel_play() -> None:
    """The agent_id selector is LLM-visible so a client can run one loop per agent."""
    from mcp_server.server import mcp_app

    schemas = {
        t.name: (t.parameters or {}).get("properties", {})
        for t in await mcp_app.list_tools()
    }
    assert "agent_id" in schemas["get_next_turn"]
    assert "agent_id" in schemas["get_instructions"]
    # The batch discovery tool takes no LLM-facing args beyond the hidden plumbing.
    assert "token" not in schemas["get_next_turns"]
    assert "db" not in schemas["get_next_turns"]


@pytest.mark.asyncio
async def test_authed_tools_hide_token_and_db_from_schema() -> None:
    """OAuth plumbing stays hidden from the LLM-visible tool schema."""
    from mcp_server.server import mcp_app

    schemas = {
        t.name: (t.parameters or {}).get("properties", {})
        for t in await mcp_app.list_tools()
    }
    for name in (
        "get_instructions",
        "get_next_turn",
        "get_next_turns",
        "submit_talk",
        "submit_action",
        "get_game_state",
        "get_chat",
    ):
        assert "token" not in schemas[name]
        assert "db" not in schemas[name]


@pytest.mark.asyncio
async def test_mcp_discovery_requires_bearer_token() -> None:
    """The MCP endpoint advertises OAuth discovery instead of a secret header.

    The server runs stateless (no in-memory session map, so a redeploy can't
    orphan a client). Stateless streamable-HTTP serves no server->client SSE
    stream, so an unauthenticated GET is 405, not the auth challenge. Real clients
    discover OAuth on the POST `initialize` path they actually use: an
    unauthenticated POST returns 401 with the Bearer challenge and the
    resource-metadata discovery URL.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # No server-push stream in stateless mode.
        assert (await client.get("/mcp")).status_code == 405

        # The OAuth challenge rides the POST initialize path real clients use.
        init = await client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "gemini-cli-mcp-client", "version": "1"},
                },
            },
        )
        assert init.status_code == 401
        challenge = init.headers["www-authenticate"]
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
async def test_get_next_turn_uses_google_identity_and_mcp_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bridge resolves the Google identity and builds the MCP connection."""
    from mcp_server import server

    captured: dict[str, object] = {}

    async def fake_sync_google_user(db: object, userinfo: object) -> SimpleNamespace:
        captured["userinfo"] = userinfo
        return SimpleNamespace(id=42, google_sub=userinfo.sub, disabled_at=None)

    async def fake_mcp_connection_for(
        db: object, user: object, *, provider: object = None, oauth_client_id: object = None
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
        agent_id: int | None = None,
        max_hold_seconds: float | None = None,
    ) -> dict[str, object]:
        captured["service_connection"] = connection
        captured["max_hold_seconds"] = max_hold_seconds
        captured["agent_id"] = agent_id
        return {"status": "waiting", "next_poll_after_seconds": 2}

    monkeypatch.setattr(server, "sync_google_user", fake_sync_google_user)
    monkeypatch.setattr(server, "mcp_connection_for", fake_mcp_connection_for)
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
    # MCP path caps the server's long-poll hold (MCP clients cut requests early).
    assert captured["max_hold_seconds"] == server._NEXT_TURN_HOLD_SECONDS


@pytest.mark.asyncio
async def test_get_next_turn_strips_duplicate_static_for_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_server import server

    async def fake_sync_google_user(db: object, userinfo: object) -> SimpleNamespace:
        return SimpleNamespace(id=42, google_sub=userinfo.sub, disabled_at=None)

    async def fake_mcp_connection_for(
        db: object, user: object, *, provider: object = None, oauth_client_id: object = None
    ) -> SimpleNamespace:
        return SimpleNamespace(id=7, key_lookup="lookup-7", user=user)

    def fake_assert_connection_usable(connection: object) -> None:
        pass

    async def fake_mark_seen(db: object, connection: object, *, key_hash: str) -> None:
        pass

    async def fake_get_next_turn(
        db: object,
        connection: object,
        *,
        agent_id: int | None = None,
        max_hold_seconds: float | None = None,
    ) -> dict[str, object]:
        return {
            "status": "your_turn",
            "match_id": "M_001",
            "turn_token": "turn-1",
            "agent_turn_token": "turn-1:1:M_001",
            "strategy": "keep it short",
            "static": {
                "match_id": "M_001",
                "rules_version": "v1",
                "rules": "rules text",
                "base_prompt": "prompt text",
                "your_strategy": "keep it short",
                "total_rounds": 7,
                "turns_per_round": 7,
                "your_agent_id": "A",
                "all_agent_ids": ["A", "B"],
                "coach_note": "stay calm",
            },
            "history": [],
            "scoreboard": [],
            "current": {"phase": "act", "turn_token": "turn-1"},
            "your_private_state": {"dice": [1, 2, 3]},
            "public_state": {"board": 1},
        }

    monkeypatch.setattr(server, "sync_google_user", fake_sync_google_user)
    monkeypatch.setattr(server, "mcp_connection_for", fake_mcp_connection_for)
    monkeypatch.setattr(server, "assert_connection_usable", fake_assert_connection_usable)
    monkeypatch.setattr(server, "mark_seen", fake_mark_seen)
    monkeypatch.setattr(server, "play_get_next_turn", fake_get_next_turn)

    result = await server.get_next_turn(token=_token(), db=object())

    assert result["status"] == "your_turn"
    assert "strategy" not in result
    assert "base_prompt" not in result["static"]
    assert "rules" not in result["static"]
    assert "your_strategy" not in result["static"]
    assert result["static"]["coach_note"] == "stay calm"
    assert result["your_private_state"] == {"dice": [1, 2, 3]}
    assert result["public_state"] == {"board": 1}
    # History is the server's rolling window — the lean wrapper preserves it as-is
    # (it only strips the duplicated static prompt text, never the live history).
    assert result["history"] == []
    assert set(result["static"]) == {
        "match_id",
        "rules_version",
        "total_rounds",
        "turns_per_round",
        "your_agent_id",
        "all_agent_ids",
        "coach_note",
    }


@pytest.mark.asyncio
async def test_get_next_turns_strips_duplicate_static_for_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_server import server

    async def fake_sync_google_user(db: object, userinfo: object) -> SimpleNamespace:
        return SimpleNamespace(id=42, google_sub=userinfo.sub, disabled_at=None)

    async def fake_mcp_connection_for(
        db: object, user: object, *, provider: object = None, oauth_client_id: object = None
    ) -> SimpleNamespace:
        return SimpleNamespace(id=7, key_lookup="lookup-7", user=user)

    def fake_assert_connection_usable(connection: object) -> None:
        pass

    async def fake_mark_seen(db: object, connection: object, *, key_hash: str) -> None:
        pass

    async def fake_get_next_turns(db: object, connection: object) -> dict[str, object]:
        return {
            "status": "your_turn",
            "turns": [
                {
                    "status": "your_turn",
                    "match_id": "M_001",
                    "turn_token": "turn-1",
                    "agent_turn_token": "turn-1:1:M_001",
                    "strategy": "keep it short",
                    "static": {
                        "match_id": "M_001",
                        "rules_version": "v1",
                        "rules": "rules text",
                        "base_prompt": "prompt text",
                        "your_strategy": "keep it short",
                        "total_rounds": 7,
                        "turns_per_round": 7,
                        "your_agent_id": "A",
                        "all_agent_ids": ["A", "B"],
                    },
                    "history": [],
                    "scoreboard": [],
                    "current": {"phase": "act", "turn_token": "turn-1"},
                    "your_private_state": {"dice": [1, 2, 3]},
                    "public_state": {"board": 1},
                }
            ],
        }

    monkeypatch.setattr(server, "sync_google_user", fake_sync_google_user)
    monkeypatch.setattr(server, "mcp_connection_for", fake_mcp_connection_for)
    monkeypatch.setattr(server, "assert_connection_usable", fake_assert_connection_usable)
    monkeypatch.setattr(server, "mark_seen", fake_mark_seen)
    monkeypatch.setattr(server, "play_get_next_turns", fake_get_next_turns)

    result = await server.get_next_turns(token=_token(), db=object())

    assert result["status"] == "your_turn"
    turn = result["turns"][0]
    assert "strategy" not in turn
    assert "base_prompt" not in turn["static"]
    assert "rules" not in turn["static"]
    assert "your_strategy" not in turn["static"]
    assert turn["static"]["match_id"] == "M_001"
    assert turn["your_private_state"] == {"dice": [1, 2, 3]}
    assert turn["public_state"] == {"board": 1}
    assert turn["history"] == []  # rolling window preserved, not stripped


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

    async def fake_mcp_connection_for(
        db: object, user: object, *, provider: object = None, oauth_client_id: object = None
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
    monkeypatch.setattr(server, "mcp_connection_for", fake_mcp_connection_for)
    monkeypatch.setattr(server, "assert_connection_usable", fake_assert_connection_usable)
    monkeypatch.setattr(server, "mark_seen", fake_mark_seen)
    monkeypatch.setattr(server, "require_agent_player", fake_require_agent_player)
    monkeypatch.setattr(server, "chat_transcript", fake_pull)

    token = _token()
    assert await server.get_chat(match_id="M_001", token=token, db=object()) == {
        "status": "ok"
    }


# ---------------------------------------------------------------------------
# Stateless-mode client identity — regression tests for spec 016
# ---------------------------------------------------------------------------


def test_client_provider_from_context_returns_none_without_active_session() -> None:
    """_client_provider_from_context() fails open (returns None) outside a live request.

    In stateless_http mode every tool-call request arrives with no persistent
    session, so session.client_params is None. The function catches this and returns
    None — expected behavior. Tool calls instead key on the DCR client_id read from
    the raw bearer JWT (see _dcr_client_id_from_request / _resolve_oauth_connection).
    """
    from mcp_server.server import _client_provider_from_context

    provider = _client_provider_from_context()
    assert provider is None


def test_dcr_client_id_from_request_decodes_bearer_jwt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_dcr_client_id_from_request() reads the per-client DCR id from the bearer JWT.

    The reference JWT FastMCP issues to a client carries its DCR client_id (a UUID)
    in the payload. We decode that payload to get a stable per-client key — unlike
    the validated AccessToken, whose client_id is the shared Google subject.
    """
    import base64
    import json

    from mcp_server import server

    payload = {"iss": "x", "client_id": "dcr-uuid-codex", "jti": "abc"}
    seg = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    jwt = f"HEADER.{seg}.SIG"
    fake_request = SimpleNamespace(headers={"authorization": f"Bearer {jwt}"})
    monkeypatch.setattr(server, "get_http_request", lambda: fake_request)

    assert server._dcr_client_id_from_request() == "dcr-uuid-codex"


def test_dcr_client_id_from_request_fails_open_without_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No/!bearer/opaque token → None, so the caller falls back to provider lookup."""
    from mcp_server import server

    for header in ({}, {"authorization": "Basic abc"}, {"authorization": "Bearer opaque"}):
        monkeypatch.setattr(
            server, "get_http_request", lambda h=header: SimpleNamespace(headers=h)
        )
        assert server._dcr_client_id_from_request() is None


@pytest.mark.asyncio
async def test_multi_connection_user_resolves_via_oauth_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user with 2+ MCP connections resolves correctly via the per-client id.

    Full chain after the fix:
      1. the DCR client_id (from the bearer JWT) is passed as oauth_client_id
      2. mcp_connection_for(oauth_client_id=...) finds the matching connection
      3. tool call succeeds — no UNKNOWN_MCP_CLIENT
    """
    from app.models.connection import ConnectionStatus

    from mcp_server import server

    EXPECTED_CLIENT_ID = "dcr-uuid-client"  # the per-client DCR id used for routing

    async def fake_sync_google_user(db: object, userinfo: object) -> SimpleNamespace:
        return SimpleNamespace(id=42, google_sub=userinfo.sub, disabled_at=None)

    async def fake_mcp_connection_for(
        db: object,
        user: object,
        *,
        provider: object = None,
        oauth_client_id: object = None,
    ) -> SimpleNamespace | None:
        # Simulates a user with 2 connections: oauth_client_id picks the right one
        if oauth_client_id == EXPECTED_CLIENT_ID:
            return SimpleNamespace(
                deleted_at=None,
                status=ConnectionStatus.ACTIVE,
                key_lookup="some-key-hash",
            )
        return None

    monkeypatch.setattr(server, "sync_google_user", fake_sync_google_user)
    monkeypatch.setattr(server, "mcp_connection_for", fake_mcp_connection_for)
    monkeypatch.setattr(server, "assert_connection_usable", lambda conn: None)

    access_token, _userinfo, connection = await server._connection_from_token(
        object(), _token(), provider=None, oauth_client_id=EXPECTED_CLIENT_ID
    )
    assert connection is not None
    assert connection.key_lookup == "some-key-hash"
