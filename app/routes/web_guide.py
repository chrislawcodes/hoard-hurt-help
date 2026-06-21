"""Guide pages, runner/setup file downloads, and legacy join redirects."""

from __future__ import annotations

import re
from pathlib import Path as FsPath
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.deps import DbSession, get_current_user
from app.routes.web_support import _is_any_admin, _redirect_to_match
from app.templating import templates

router = APIRouter(tags=["web"])

_DOCS_DIR = FsPath("docs")
_GUIDE_NAME = re.compile(r"^[a-z0-9-]+$")


@router.get("/guide/{name}", response_class=HTMLResponse)
async def guide(name: Annotated[str, Path()], request: Request, db: DbSession):
    """Render a setup doc from docs/<name>.md inside the site chrome."""
    if not _GUIDE_NAME.match(name):
        raise HTTPException(404)
    path = _DOCS_DIR / f"{name}.md"
    if not path.is_file():
        raise HTTPException(404)
    user = await get_current_user(request, db)
    return templates.TemplateResponse(
        request,
        "guide.html",
        {
            "user": user,
            "is_admin": _is_any_admin(user),
            "title": name.replace("-", " ").title(),
            "body": path.read_text(encoding="utf-8"),
        },
    )


# Chained-session setup file download. ONE script drives every CLI provider for a
# connection: agentludum_connector.py. Allowlisted by exact filename below; the
# path never comes from the request, so there is no traversal surface.
_UNIFIED_RUNNER = FsPath("scripts/agentludum_connector.py")
_AGENT_RUNNERS: dict[str, FsPath] = {
    "agentludum_connector.py": _UNIFIED_RUNNER,
}


def _serve_agent_file(name: str) -> FileResponse:
    path = _AGENT_RUNNERS.get(name)
    if path is None or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="text/x-python", filename=name)


@router.get("/setup-files/{name}", include_in_schema=False)
async def agent_setup_file(name: Annotated[str, Path()]) -> FileResponse:
    """Serve a setup script so the setup `curl` fetches it.

    Allowlisted by exact filename — the path never comes from the request, so
    there's no traversal surface. Single source of truth: this streams the
    repo's scripts/<name>, so the downloaded file always matches this server.
    """
    return _serve_agent_file(name)


@router.get("/runners/{name}", include_in_schema=False)
async def agent_runner_script(name: Annotated[str, Path()]) -> FileResponse:
    return _serve_agent_file(name)


@router.get("/games/{match_id}/join", response_class=HTMLResponse)
async def legacy_join_form_redirect(
    match_id: Annotated[str, Path()],
    db: DbSession,
):
    return await _redirect_to_match(db, match_id, suffix="/join")


@router.post("/games/{match_id}/join", include_in_schema=False)
async def legacy_join_submit_redirect(
    match_id: Annotated[str, Path()],
    db: DbSession,
):
    return RedirectResponse(
        url=(await _redirect_to_match(db, match_id, suffix="/join")).headers["location"],
        status_code=status.HTTP_308_PERMANENT_REDIRECT,
    )
