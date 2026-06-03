"""Global request logging and 500 handling."""

from __future__ import annotations

import logging
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

logger = logging.getLogger(__name__)


def _session_user_id(request: Request) -> int | None:
    session = request.scope.get("session")
    if isinstance(session, dict):
        user_id = session.get("user_id")
        return user_id if isinstance(user_id, int) else None
    return None


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
