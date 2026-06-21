"""Shared constructor for the nested API error envelope.

Every JSON API error in this app uses the same ``detail`` shape:

    {"error": {"code": ..., "message": ..., "details": {...}}}

This module is the one place that builds it, so the shape stays consistent.
"""

from __future__ import annotations

from fastapi import HTTPException


def api_error(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict | None = None,
) -> HTTPException:
    """Build an ``HTTPException`` carrying the standard nested error envelope.

    ``details`` defaults to an empty dict so the key is always present.
    """
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            }
        },
    )
