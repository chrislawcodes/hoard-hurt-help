"""FastAPI app factory and uvicorn entry point.

Routes and middleware are added by each phase.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware

from app.cache_warmup import warm_homepage_caches
from app.config import settings
from app.db_bootstrap import prepare_database_for_upgrade, verify_required_tables
from app.engine.scheduler import registry as scheduler_registry
from app.canonical_host import CanonicalHostMiddleware, canonical_host_of
from app.request_logging import install_request_logging
from app.routes import (
    admin_api,
    admin_web,
    agent_api,
    agent_next_turn,
    agents_lifecycle,
    agents_setup,
    agents_status,
    auth as auth_routes,
    connections_credentials,
    connections_lifecycle,
    connections_setup,
    game_admin_api,
    game_admin_web,
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

logger = logging.getLogger(__name__)

# Optional deep logging for the MCP OAuth auth layer (FastMCP token swap and
# upstream validation). Off by default; set MCP_AUTH_DEBUG=1 to surface the
# precise reason a bearer token is rejected on /mcp, without flooding normal
# request logs. Safe to leave wired in — it only raises one sub-logger's level.
if os.getenv("MCP_AUTH_DEBUG", "").strip() == "1":
    logging.getLogger("fastmcp.server.auth").setLevel(logging.DEBUG)
    logger.warning("MCP_AUTH_DEBUG=1: fastmcp auth-layer DEBUG logging is ON")


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


def _check_oauth_config() -> None:
    """Validate Google OAuth credentials at startup.

    In a real deployment (detected by RAILWAY_ENVIRONMENT_ID), missing OAuth
    credentials raise RuntimeError so the process exits before accepting traffic.
    In local dev, log a WARNING and continue — sign-in simply won't work.
    Tests are skipped entirely (PYTEST_CURRENT_TEST is set by pytest).

    Missing vars are named explicitly so the operator can fix them without
    reading source code.
    """
    if os.getenv("PYTEST_CURRENT_TEST"):
        return

    problems = [
        f"{var} is not set"
        for var, val in (
            ("GOOGLE_CLIENT_ID", settings.google_client_id),
            ("GOOGLE_CLIENT_SECRET", settings.google_client_secret),
        )
        if not val.strip()
    ]
    # The MCP OAuth server advertises base_url in its discovery documents, so a
    # localhost/empty value can't work for a remote MCP client. Validate it as
    # part of the OAuth config check so a real deployment fails loud rather than
    # serving broken discovery.
    base_url = settings.base_url.strip()
    if (
        not base_url
        or base_url.startswith("http://localhost")
        or base_url.startswith("http://127.")
    ):
        problems.append(
            f"BASE_URL must be your public https:// host for MCP OAuth discovery (got {settings.base_url!r})"
        )

    # A stable MCP_JWT_SIGNING_KEY decouples MCP token signing + store encryption
    # from GOOGLE_CLIENT_SECRET. Without it the keys silently fall back to the
    # Google secret, so rotating that secret would invalidate every login and make
    # every stored upstream token undecryptable in one shot. Require it in real
    # deployments so this can never silently regress.
    if not settings.mcp_jwt_signing_key.strip():
        problems.append(
            "MCP_JWT_SIGNING_KEY is not set (use a stable 32+ char random secret; "
            "it decouples MCP token signing/encryption from GOOGLE_CLIENT_SECRET)"
        )

    if not problems:
        return

    problems_str = "; ".join(problems)
    if os.getenv("RAILWAY_ENVIRONMENT_ID"):
        # Fail loud BEFORE serving traffic so /mcp never starts in a broken,
        # fail-open state (the GoogleProvider dev-placeholder fallback must never
        # run in a real deployment).
        raise RuntimeError(
            f"OAuth configuration is incomplete: {problems_str}. "
            "Set them in your Railway service variables before deploying."
        )
    logger.warning(
        "OAuth configuration incomplete — sign-in will not work. Fix: %s",
        problems_str,
    )


def _check_platform_admin_config() -> None:
    """Warn at startup if no platform admins are configured.

    Advisory only — the app still starts. Without a floor admin no one can
    reach /admin/matches, so operators should notice quickly. Tests are skipped.
    """
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    if not settings.platform_admin_emails_set:
        logger.warning(
            "No platform admins configured — set PLATFORM_ADMIN_EMAILS "
            "to grant admin access. Without it /admin/matches is unreachable."
        )


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
        verify_required_tables(settings.database_url)

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
    except ImportError:
        # MCP SDK not installed in this environment — /mcp will be unavailable.
        logger.warning("MCP SDK not installed; /mcp endpoint disabled")
    except Exception:
        # MCP SDK is present but broken (bad install, version conflict, etc).
        logger.exception("Failed to initialize MCP server; /mcp endpoint disabled")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _check_oauth_config()
        _check_platform_admin_config()
        await _upgrade_database()
        await scheduler_registry.resume_active_games_on_startup()
        scheduler_registry.start_poller()  # auto-start games when their time comes
        # Pre-build the front page's caches in the background so the first
        # visitor after a deploy isn't the one who pays the full rebuild. Kept
        # off the startup path (a task, not an await) so it never delays the
        # server coming up; held on app.state so it isn't garbage-collected.
        app.state.cache_warmup_task = asyncio.create_task(warm_homepage_caches())
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
    # Outermost: refuse the Railway domain (and any non-canonical host) in real
    # deployments, so the only working address is the canonical one. Without this,
    # a client that registers the *.up.railway.app URL connects but fails OAuth
    # sign-in with a confusing "protected resource mismatch". The deploy health
    # check (/healthz) is always allowed. Off outside a real deployment, so local
    # dev and tests (Host: testserver) are unaffected.
    app.add_middleware(
        CanonicalHostMiddleware,
        canonical_host=canonical_host_of(settings.base_url),
        enabled=bool(os.getenv("RAILWAY_ENVIRONMENT_ID")),
    )

    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(auth_routes.router)
    app.include_router(agent_api.router, prefix="/api/matches/{match_id}")
    app.include_router(agent_api.router, prefix="/api/games/{match_id}")
    app.include_router(agent_next_turn.router)
    app.include_router(agents_setup.router, prefix="/me/agents")
    app.include_router(agents_status.router, prefix="/me/agents")
    app.include_router(agents_lifecycle.router, prefix="/me/agents")
    app.include_router(connections_setup.router, prefix="/me/connections")
    app.include_router(connections_credentials.router, prefix="/me/connections")
    app.include_router(connections_lifecycle.router, prefix="/me/connections")
    # Human-page routers resolve the smart Play CTA (nav + hero) per request.
    # API/agent/SSE routers are left out — they render no nav.
    page_deps = [Depends(populate_nav_cta)]
    app.include_router(web_routes.router, dependencies=page_deps)
    app.include_router(handle_web.router, dependencies=page_deps)
    app.include_router(admin_web.router, dependencies=page_deps)
    app.include_router(game_admin_web.router, dependencies=page_deps)
    app.include_router(admin_api.router)
    app.include_router(game_admin_api.router)
    app.include_router(sse_routes.router)
    app.include_router(spectator_api.router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse("app/static/favicon.svg", media_type="image/svg+xml")

    if mcp_asgi_app is not None:
        # Mount the FastMCP app last so its catch-all root mount does not shadow
        # the rest of the FastAPI routes.
        app.mount("/", mcp_asgi_app, name="mcp")

    return app


app = create_app()
