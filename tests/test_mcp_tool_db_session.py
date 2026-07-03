"""Regression: MCP tools must receive a real DB session from FastMCP's DI.

FastMCP resolves a ``Depends()`` value with the ``uncalled_for`` library, which
enters the dependency as an async *context manager*. Unlike FastAPI it does NOT
iterate a bare async generator — so depending on the app's ``get_session``
(a generator) left every tool holding the raw generator object, and the first
tool to actually touch the DB blew up with::

    'async_generator' object has no attribute 'execute'

The tools now depend on ``_session_scope`` (an ``@asynccontextmanager``). These
tests exercise the real resolution path that the existing db=fake tests skip.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager

from fastmcp.dependencies import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from mcp_server import server


def test_session_scope_is_an_async_context_manager() -> None:
    """uncalled_for can only enter an async context manager. A bare async
    generator (the old get_session) would fall through untouched."""
    scope = server._session_scope()
    assert isinstance(scope, AbstractAsyncContextManager)


async def test_di_resolves_session_scope_to_a_real_session() -> None:
    """Resolving the tool dependency through FastMCP's DI yields an AsyncSession,
    not the async generator that caused the production error."""
    from uncalled_for.resolution import resolved_dependencies

    async def _fake_tool(
        db: AsyncSession = Depends(server._session_scope),
    ) -> None: ...

    async with resolved_dependencies(_fake_tool) as args:
        assert isinstance(args["db"], AsyncSession)
