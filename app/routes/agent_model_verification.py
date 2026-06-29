"""Connection-scoped model-verification channels (slice 2).

Down: GET /api/agent/model-worklist — the (provider, model) pairs this connection
should verify (provider defaults + the user's matching preferred models, minus
anything checked within the refresh window).

Up: POST /api/agent/model-verification — the connector reports outcomes; the server
upserts the cache (sanitizing error text). The play-time failure path (slice 3)
extends this endpoint.

Dedicated endpoints, not fields on the turn poll: the idle connector discards the
poll body, and a missed-deadline turn never POSTs a submit (see spec Reporting
channels).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.deps import DbSession, require_connection
from app.engine.model_verification import compute_worklist, record_results
from app.models.connection import Connection

router = APIRouter(prefix="/api/agent", tags=["agent"])


class _ModelResult(BaseModel):
    provider: str
    model: str
    outcome: str  # "verified" | "failed" | "timeout"
    error_text: str | None = None


class _ModelVerificationReport(BaseModel):
    results: list[_ModelResult] = []


@router.get("/model-worklist", response_model=None)
async def model_worklist(
    connection: Annotated[Connection, Depends(require_connection)],
    db: DbSession,
) -> dict[str, object]:
    return {"worklist": await compute_worklist(db, connection)}


@router.post("/model-verification", status_code=204)
async def model_verification(
    body: _ModelVerificationReport,
    connection: Annotated[Connection, Depends(require_connection)],
    db: DbSession,
) -> None:
    await record_results(
        db, connection, [result.model_dump() for result in body.results]
    )
    await db.commit()
