"""FastAPI app factory and uvicorn entry point.

Routes and middleware are added by each phase.
"""

import asyncio
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db_bootstrap import prepare_database_for_upgrade
from app.engine.scheduler import registry as scheduler_registry
from app.request_logging import install_request_logging
from app.routes import (
    admin_api,
    admin_web,
    agent_api,
    agent_next_turn,
    auth as auth_routes,
    bots_web,
    handle_web,
    spectator_api,
    sse as sse_routes,
    web as web_routes,
)
from app.routes.nav_context import populate_nav_cta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def _should_run_startup_migrations() -> bool:
    """Skip automatic migrations in tests and on Railway; run them elsewhere."""
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    # Railway runs migrations in preDeployCommand, before the container starts.
    # The runtime env marker keeps the app from repeating the same work and
    # stalling the healthcheck window.
    if os.getenv("RAILWAY_ENVIRONMENT_ID"):
        return False
    return os.getenv("SKIP_STARTUP_MIGRATIONS", "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }


def _alembic_config() -> Config:
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    return cfg


async def _upgrade_database() -> None:
    if not _should_run_startup_migrations():
        return

    def _run_upgrade() -> None:
        cfg = _alembic_config()
        prepare_database_for_upgrade(cfg, settings.database_url)
        command.upgrade(cfg, "head")

    await asyncio.to_thread(_run_upgrade)


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
        await _upgrade_database()
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

    # Swagger / ReDoc / OpenAPI are a dev convenience, not a public surface —
    # exposing them would hand anyone a map of every endpoint (admin, auth,
    # internal). Agents reach the API directly; they don't need the docs. So serve
    # them only outside production. cookie_secure is our prod/HTTPS signal, so in
    # prod /docs, /redoc and /openapi.json all 404.
    _expose_docs = not settings.cookie_secure
    app = FastAPI(
        title="Agent Ludum API",
        version="0.1.0",
        description="Internal HTTP API agents use to play on Agent Ludum.",
        lifespan=lifespan,
        docs_url="/docs" if _expose_docs else None,
        redoc_url="/redoc" if _expose_docs else None,
        openapi_url="/openapi.json" if _expose_docs else None,
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.cookie_secure,  # Secure cookie in prod (COOKIE_SECURE=true)
        session_cookie="hhh_session",
    )
    install_request_logging(app)

    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(auth_routes.router)
    app.include_router(agent_api.router, prefix="/api/matches/{match_id}")
    app.include_router(agent_api.router, prefix="/api/games/{match_id}")
    app.include_router(agent_next_turn.router)
    # Human-page routers resolve the smart Play CTA (nav + hero) per request.
    # API/agent/SSE routers are left out — they render no nav.
    page_deps = [Depends(populate_nav_cta)]
    app.include_router(web_routes.router, dependencies=page_deps)
    app.include_router(handle_web.router, dependencies=page_deps)
    app.include_router(bots_web.router, dependencies=page_deps)
    app.include_router(admin_web.router, dependencies=page_deps)
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
