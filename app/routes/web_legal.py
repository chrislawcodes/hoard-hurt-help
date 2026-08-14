"""Public legal pages (`/privacy`, `/terms`).

Both are static prose with no per-user state, but they still render through the
normal template stack so they carry the site nav and footer — a legal page that
drops the reader out of the site chrome reads like a different site.

No auth dependency, for the same reason `/contact` has none: a signed-out
visitor deciding whether to sign up is exactly the reader these pages exist for,
and a locked-out user has to be able to read the terms they were disabled under.

Both pages point at `/contact` rather than printing the address, keeping the
mailbox on exactly one page of the site (see ``web_contact``).

The last-updated date lives here rather than in the templates so the two pages
cannot drift apart, and so a test can assert both carry one.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.deps import DbSession, get_current_user
from app.routes.web_support import _is_any_admin
from app.templating import templates

router = APIRouter(tags=["web"])

# Bump this whenever the wording of either page changes in a way a reader would
# care about. Both pages show it, so they always agree on their own vintage.
LAST_UPDATED = "August 14, 2026"


async def _legal_context(request: Request, db: DbSession) -> dict[str, object]:
    """Shared template context for both legal pages."""
    user = await get_current_user(request, db)
    return {
        "user": user,
        "is_admin": _is_any_admin(user),
        "last_updated": LAST_UPDATED,
    }


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request, db: DbSession) -> HTMLResponse:
    """Render the public privacy policy."""
    return templates.TemplateResponse(
        request, "legal/privacy.html", await _legal_context(request, db)
    )


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request, db: DbSession) -> HTMLResponse:
    """Render the public terms of service."""
    return templates.TemplateResponse(
        request, "legal/terms.html", await _legal_context(request, db)
    )


__all__ = ["router", "privacy", "terms", "LAST_UPDATED"]
