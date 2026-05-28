"""FastAPI app factory and uvicorn entry point.

Routes and middleware are added by each phase.
"""

import logging

from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
from fastapi.staticfiles import StaticFiles
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


def create_app() -> FastAPI:
    app = FastAPI(
        title="Hoard-Hurt-Help",
        version="0.1.0",
        description="Multiplayer Prisoner's Dilemma for LLM agents",
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

    # Mount the MCP server at /mcp. Import inside create_app so tests that
    # bypass the MCP transport can still build the app.
    try:
        from mcp_server.server import asgi_app as mcp_asgi_app

        app.mount("/mcp", mcp_asgi_app, name="mcp")
    except Exception:
        # MCP SDK not importable in this env — skip mounting.
        pass

    @app.on_event("startup")
    async def _resume_games_on_startup() -> None:
        await scheduler_registry.resume_active_games_on_startup()

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
