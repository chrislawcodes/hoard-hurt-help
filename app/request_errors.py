"""Global request error logging and 500 handling."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)


def _session_user_id(request: Request) -> Any:
    session = request.scope.get("session")
    if isinstance(session, dict):
        return session.get("user_id")
    return None


def install_request_error_logging(app: FastAPI) -> None:
    """Log unhandled exceptions and return a stable 500 response."""

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        incident_id = uuid4().hex[:8]
        logger.error(
            "UNHANDLED REQUEST ERROR incident=%s method=%s path=%s user_id=%s query=%s",
            incident_id,
            request.method,
            request.url.path,
            _session_user_id(request),
            str(request.query_params),
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return PlainTextResponse(
            "Internal Server Error",
            status_code=500,
            headers={"X-Request-Id": incident_id},
        )
