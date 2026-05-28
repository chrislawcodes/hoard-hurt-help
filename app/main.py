"""FastAPI app factory and uvicorn entry point.

Routes and middleware are added by each phase.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.engine.scheduler import registry as scheduler_registry
from app.routes import agent_api, auth as auth_routes


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

    @app.on_event("startup")
    async def _resume_games_on_startup() -> None:
        await scheduler_registry.resume_active_games_on_startup()

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
