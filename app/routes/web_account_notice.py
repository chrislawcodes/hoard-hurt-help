"""Public account-notice page (`/disabled`).

This page is shown to users whose account has been disabled. It deliberately has
NO auth dependency: a disabled user must still be able to see why they were
locked out.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.deps import DbSession, get_current_user
from app.templating import templates

router = APIRouter(tags=["web"])


@router.get("/disabled", response_class=HTMLResponse)
async def account_disabled(
    request: Request,
    db: DbSession,
) -> HTMLResponse:
    user = await get_current_user(request, db)
    return templates.TemplateResponse(
        request,
        "disabled.html",
        {"user": user, "is_admin": False},
    )


__all__ = ["router", "account_disabled"]
