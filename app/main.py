"""FastAPI app factory and uvicorn entry point.

Routes and middleware are added by each phase.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.engine.scheduler import registry as scheduler_registry
from app.routes import (
    admin_api,
    admin_web,
    agent_api,
    auth as auth_routes,
    spectator_api,
    sse as sse_routes,
    web as web_routes,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def create_app() -> FastAPI:
    # Resolve the MCP sub-app up front. Its Starlette lifespan starts the
    # streamable-HTTP session manager, and a mounted ASGI app's lifespan is
    # NOT run by the parent automatically — we have to drive it ourselves
    # below, or every MCP session dies with "Session terminated".
    mcp_asgi_app: Starlette | None = None
    try:
        from mcp_server.server import asgi_app

        mcp_asgi_app = asgi_app
    except Exception:
        # MCP SDK not importable in this env — skip mounting.
        pass

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await scheduler_registry.resume_active_games_on_startup()
        scheduler_registry.start_poller()  # auto-start games when their time comes
        try:
            if mcp_asgi_app is not None:
                async with mcp_asgi_app.router.lifespan_context(app):
                    yield
            else:
                yield
        finally:
            scheduler_registry.stop_poller()

    app = FastAPI(
        title="Hoard-Hurt-Help",
        version="0.1.0",
        description="Multiplayer Prisoner's Dilemma for LLM agents",
        lifespan=lifespan,
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=False,  # set True behind HTTPS in prod via env
        session_cookie="hhh_session",
    )

    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(auth_routes.router)
    app.include_router(agent_api.router)
    app.include_router(web_routes.router)
    app.include_router(admin_web.router)
    app.include_router(admin_api.router)
    app.include_router(sse_routes.router)
    app.include_router(spectator_api.router)

    if mcp_asgi_app is not None:
        app.mount("/mcp", mcp_asgi_app, name="mcp")

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse("app/static/favicon.svg", media_type="image/svg+xml")

    return app


app = create_app()
