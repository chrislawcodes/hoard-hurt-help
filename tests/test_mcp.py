"""MCP server smoke tests — tool registration + HTTP wrapping shape."""

import pytest


@pytest.mark.asyncio
async def test_mcp_tools_registered():
    """The three tools we ship are present on the FastMCP instance."""
    from mcp_server.server import mcp_app

    tool_names = {tool.name for tool in await mcp_app.list_tools()}
    assert {"get_turn", "submit_action", "get_game_state"}.issubset(tool_names)


@pytest.mark.asyncio
async def test_pull_tools_registered():
    """The four opt-in detail tools (feature 002) are present."""
    from mcp_server.server import mcp_app

    tool_names = {tool.name for tool in await mcp_app.list_tools()}
    assert {
        "get_opponent_history",
        "get_chat",
        "get_turn_detail",
        "get_standings",
    }.issubset(tool_names)


@pytest.mark.asyncio
async def test_authed_tools_hide_key_from_schema():
    """The key is a connection header, never an LLM-visible parameter."""
    from mcp_server.server import mcp_app

    schemas = {t.name: (t.inputSchema or {}).get("properties", {}) for t in await mcp_app.list_tools()}
    for name in ("get_turn", "submit_action"):
        assert "agent_key" not in schemas[name], f"{name} still exposes agent_key"
        assert "ctx" not in schemas[name], f"{name} leaks the injected context"


# --- Fakes for exercising header-based auth without the MCP transport ---


class _FakeHeaders:
    def __init__(self, mapping):
        self._m = {k.lower(): v for k, v in mapping.items()}

    def get(self, key, default=None):
        return self._m.get(key.lower(), default)


class _FakeCtx:
    """Stands in for mcp Context; only request_context.request.headers is read."""

    def __init__(self, headers=None):
        request = type("R", (), {"headers": _FakeHeaders(headers or {})})()
        self.request_context = type("RC", (), {"request": request})()


class _FakeResp:
    def __init__(self, data):
        self._data = data
        self.is_success = True
        self.status_code = 200

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, capture):
        self.capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None, params=None):
        self.capture.update(method="GET", url=url, headers=headers, params=params)
        return _FakeResp({"status": "waiting"})

    async def post(self, url, headers=None, json=None):
        self.capture.update(method="POST", url=url, headers=headers, body=json)
        return _FakeResp({"received_at": "now"})


@pytest.mark.asyncio
async def test_get_turn_forwards_connection_key(monkeypatch):
    """get_turn pulls X-Agent-Key off the connection and forwards it upstream."""
    from mcp_server import server

    cap: dict = {}
    monkeypatch.setattr(server, "_client", lambda: _FakeClient(cap))
    ctx = _FakeCtx({"X-Agent-Key": "sk_game_abc123"})

    await server.get_turn(match_id="G_0001", ctx=ctx)

    assert cap["url"] == "/api/matches/G_0001/turn"
    assert cap["headers"]["X-Agent-Key"] == "sk_game_abc123"


@pytest.mark.asyncio
async def test_submit_action_forwards_connection_key(monkeypatch):
    from mcp_server import server

    cap: dict = {}
    monkeypatch.setattr(server, "_client", lambda: _FakeClient(cap))
    ctx = _FakeCtx({"X-Agent-Key": "sk_game_abc123"})

    await server.submit_action(
        match_id="G_0001",
        action="HOARD",
        target_id=None,
        message="hi",
        turn_token="tok_1",
        ctx=ctx,
    )

    assert cap["headers"]["X-Agent-Key"] == "sk_game_abc123"
    assert cap["body"]["action"] == "HOARD"


@pytest.mark.asyncio
async def test_missing_connection_key_raises(monkeypatch):
    """No header configured → a clear error, not a silent unauthenticated call."""
    from mcp_server import server

    monkeypatch.setattr(server, "_client", lambda: _FakeClient({}))
    with pytest.raises(RuntimeError, match="X-Agent-Key"):
        await server.get_turn(match_id="G_0001", ctx=_FakeCtx({}))


def test_mcp_asgi_app_constructed():
    """The streamable_http_app is built and importable."""
    from mcp_server.server import asgi_app

    assert asgi_app is not None


def test_mcp_mounted_on_fastapi():
    """The /mcp route is mounted by app.main."""
    from app.main import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/mcp" in paths


@pytest.mark.asyncio
async def test_pull_tools_forward_connection_key(monkeypatch):
    """Each pull tool reads X-Agent-Key off the connection and hits the right URL."""
    from mcp_server import server

    ctx = _FakeCtx({"X-Agent-Key": "sk_game_abc123"})

    cap1: dict = {}
    monkeypatch.setattr(server, "_client", lambda: _FakeClient(cap1))
    await server.get_opponent_history(match_id="G_0001", opponent_id="AI_2", ctx=ctx)
    assert cap1["url"] == "/api/matches/G_0001/history/opponents/AI_2"
    assert cap1["headers"]["X-Agent-Key"] == "sk_game_abc123"

    cap2: dict = {}
    monkeypatch.setattr(server, "_client", lambda: _FakeClient(cap2))
    await server.get_chat(match_id="G_0001", ctx=ctx, since="2.3")
    assert cap2["url"] == "/api/matches/G_0001/chat"
    assert cap2["params"] == {"since": "2.3"}

    cap3: dict = {}
    monkeypatch.setattr(server, "_client", lambda: _FakeClient(cap3))
    await server.get_turn_detail(match_id="G_0001", round=3, turn=4, ctx=ctx)
    assert cap3["url"] == "/api/matches/G_0001/turns/3/4"

    cap4: dict = {}
    monkeypatch.setattr(server, "_client", lambda: _FakeClient(cap4))
    await server.get_standings(match_id="G_0001", ctx=ctx)
    assert cap4["url"] == "/api/matches/G_0001/standings"
