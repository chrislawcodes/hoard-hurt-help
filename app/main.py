"""FastAPI app factory and uvicorn entry point.

This file stays minimal — routes and middleware are added by feature phases.
Phase 1 just gets the server booting with `/healthz`.
"""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Construct the FastAPI app. Imported by uvicorn and by tests."""
    app = FastAPI(
        title="Hoard-Hurt-Help",
        version="0.1.0",
        description="Multiplayer Prisoner's Dilemma for LLM agents",
    )

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
