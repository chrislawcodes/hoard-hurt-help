"""Public contact page (`/contact`).

The site's one reachable address. It gets its own page rather than living in the
footer chrome so the address sits in exactly one page's HTML instead of every
page's — the footer links here, so a scraper crawling the site finds a link, not
a mailbox.

No auth dependency: a signed-out visitor and a locked-out user both have to be
able to reach it (`/disabled` links here, and appealing a disable is the one
thing that page exists to enable).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.deps import DbSession, get_current_user
from app.routes.web_support import _is_any_admin
from app.templating import templates

router = APIRouter(tags=["web"])

# Single source of truth for the public contact address.
CONTACT_EMAIL = "agentludum@gmail.com"


@router.get("/contact", response_class=HTMLResponse)
async def contact(request: Request, db: DbSession) -> HTMLResponse:
    """Render the public contact page."""
    user = await get_current_user(request, db)
    return templates.TemplateResponse(
        request,
        "contact.html",
        {
            "user": user,
            "is_admin": _is_any_admin(user),
            "contact_email": CONTACT_EMAIL,
        },
    )


__all__ = ["router", "contact", "CONTACT_EMAIL"]
