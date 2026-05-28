"""MCP server smoke tests — tool registration + HTTP wrapping shape."""

import pytest


@pytest.mark.asyncio
async def test_mcp_tools_registered():
    """The three tools we ship are present on the FastMCP instance."""
    from mcp_server.server import mcp_app

    tool_names = {tool.name for tool in await mcp_app.list_tools()}
    assert {"get_turn", "submit_action", "get_game_state"}.issubset(tool_names)


def test_mcp_asgi_app_constructed():
    """The streamable_http_app is built and importable."""
    from mcp_server.server import asgi_app

    assert asgi_app is not None


def test_mcp_mounted_on_fastapi():
    """The /mcp route is mounted by app.main."""
    from app.main import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/mcp" in paths
