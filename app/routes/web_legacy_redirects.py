"""Legacy `/play/...` redirects to their current `/games/...` paths.

These keep old bookmarked and external links alive by 301-redirecting to the
canonical game URLs.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, status
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(tags=["web"])


@router.get("/play/{game}", response_class=HTMLResponse)
async def legacy_play_redirect(game: Annotated[str, Path()]):
    return RedirectResponse(url=f"/games/{game}", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@router.get("/play/{game}/upcoming", response_class=HTMLResponse)
async def legacy_play_upcoming_redirect(game: Annotated[str, Path()]):
    return RedirectResponse(
        url=f"/games/{game}/upcoming", status_code=status.HTTP_301_MOVED_PERMANENTLY
    )


__all__ = ["router", "legacy_play_redirect", "legacy_play_upcoming_redirect"]
