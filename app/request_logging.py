"""Global request logging, incident capture, and 500 handling."""

from __future__ import annotations

import json
import logging
import traceback
from time import monotonic
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from app.models.request_incident import RequestIncident

logger = logging.getLogger(__name__)

_TRACE_CONTEXT_KEY = "request_trace_context"


def _session_user_id(request: Request) -> int | None:
    session = request.scope.get("session")
    if isinstance(session, dict):
        user_id = session.get("user_id")
        return user_id if isinstance(user_id, int) else None
    return None


def set_request_trace_context(request: Request, **fields: Any) -> None:
    """Attach route-local context to the request for later incident capture."""
    ctx = getattr(request.state, _TRACE_CONTEXT_KEY, None)
    if not isinstance(ctx, dict):
        ctx = {}
    for key, value in fields.items():
        if value is not None:
            ctx[key] = value
    setattr(request.state, _TRACE_CONTEXT_KEY, ctx)


def _trace_context(request: Request) -> dict[str, Any]:
    ctx = getattr(request.state, _TRACE_CONTEXT_KEY, None)
    return ctx if isinstance(ctx, dict) else {}


def _path_params(request: Request) -> dict[str, Any]:
    params = request.path_params
    return params if isinstance(params, dict) else {}


def _int_path_param(request: Request, name: str) -> int | None:
    value = _path_params(request).get(name)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


async def _record_incident(
    request: Request,
    *,
    request_id: str,
    exc: Exception,
    status_code: int,
) -> None:
    from app import db as app_db

    ctx = _trace_context(request)
    path_params = _path_params(request)
    stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    match_id = ctx.get("match_id") or path_params.get("match_id")
    bot_id = ctx.get("bot_id") or _int_path_param(request, "bot_id")
    player_id = ctx.get("player_id") or _int_path_param(request, "player_id")
    payload = {
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "query_string": str(request.query_params) or None,
        "user_id": _session_user_id(request),
        "match_id": match_id,
        "bot_id": bot_id,
        "player_id": player_id,
        "stage": ctx.get("stage"),
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "stacktrace": stack,
        "context_json": (
            json.dumps(
                {
                    **ctx,
                    **({"path_params": path_params} if path_params else {}),
                },
                sort_keys=True,
                default=str,
            )
            if (ctx or path_params)
            else None
        ),
    }
    try:
        async with app_db.SessionLocal() as db:
            db.add(RequestIncident(**payload))
            await db.commit()
    except SQLAlchemyError:
        # fail-open: advisory only — persisting an incident must never crash the
        # request that already failed; log and move on.
        logger.exception(
            "Failed to persist request incident request_id=%s path=%s status=%s",
            request_id,
            request.url.path,
            status_code,
        )


async def record_background_incident(
    *,
    source: str,
    exc: BaseException,
    match_id: str | None = None,
    stage: str | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """Persist a RequestIncident for a non-HTTP background failure.

    Background tasks (the turn-loop scheduler, the pollers) have no Request, so
    until now their crashes never reached ``request_incidents`` — a frozen match
    looked completely silent in the DB. This writes the same row shape with task
    sentinels in the HTTP-only columns (``method='TASK'``, ``path=<source>``) so
    a ``SELECT ... WHERE match_id=`` surfaces background crashes alongside
    request failures.
    """
    from app import db as app_db

    stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    payload = {
        "request_id": uuid4().hex[:8],
        "method": "TASK",
        "path": source[:255],
        "query_string": None,
        "user_id": None,
        "match_id": match_id,
        "bot_id": None,
        "player_id": None,
        "stage": stage,
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "stacktrace": stack,
        "context_json": (
            json.dumps(context, sort_keys=True, default=str) if context else None
        ),
    }
    try:
        async with app_db.SessionLocal() as db:
            db.add(RequestIncident(**payload))
            await db.commit()
    except SQLAlchemyError:
        # fail-open: advisory only — a background task's incident row is best
        # effort; failing to write it must not crash the scheduler/poller.
        logger.exception(
            "Failed to persist background incident source=%s match_id=%s",
            source,
            match_id,
        )


def install_request_logging(app: FastAPI) -> None:
    """Log every request and stamp failures with a request id."""

    @app.middleware("http")
    async def _log_requests(request: Request, call_next) -> Response:
        request_id = uuid4().hex[:8]
        start = monotonic()
        request.state.request_id = request_id
        logger.info(
            "request start id=%s method=%s path=%s user_id=%s query=%s",
            request_id,
            request.method,
            request.url.path,
            _session_user_id(request),
            str(request.query_params),
        )
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception(
                "request error id=%s method=%s path=%s user_id=%s query=%s",
                request_id,
                request.method,
                request.url.path,
                _session_user_id(request),
                str(request.query_params),
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            await _record_incident(request, request_id=request_id, exc=exc, status_code=500)
            return PlainTextResponse(
                f"Internal Server Error\nRequest ID: {request_id}",
                status_code=500,
                headers={"X-Request-Id": request_id},
            )

        response.headers["X-Request-Id"] = request_id
        duration_ms = int((monotonic() - start) * 1000)
        logger.info(
            "request end id=%s method=%s path=%s status=%s ms=%s user_id=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            _session_user_id(request),
        )
        return response
