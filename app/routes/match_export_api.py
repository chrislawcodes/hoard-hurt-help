"""Game-scoped match exports — open to any signed-in player.

Exports are a research and review tool, so every signed-in player may pull any
match. What the export *contains* is narrowed instead of who may ask: a
non-admin's payload omits other players' strategy prompts and stops at the last
resolved turn. See ``app.read_models.match_export``.

Creating and cancelling matches used to live here too. Both were exact
duplicates of ``/api/admin/matches`` and ``/api/admin/matches/{id}/cancel``, so
they were removed rather than re-gated — leaving a second player-reachable
create route would have bypassed the per-user active-match cap.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.deps import DbSession, require_user
from app.models.user import User, UserRole
from app.read_models.match_export import ExportViewer
from app.routes.admin_match_actions import export_match_csv, export_match_json
from app.routes.match_authz import ExportableMatch

router = APIRouter(prefix="/api/game-admin/{game}", tags=["match-export"])


def _viewer(user: User) -> ExportViewer:
    return ExportViewer(
        user_id=user.id, is_platform_admin=user.role == UserRole.ADMIN
    )


@router.get("/matches/{match_id}/export.csv")
async def export_csv(
    db: DbSession,
    match: ExportableMatch,
    user: Annotated[User, Depends(require_user)],
) -> StreamingResponse:
    return await export_match_csv(db, match.id, viewer=_viewer(user))


@router.get("/matches/{match_id}/export.json")
async def export_json(
    db: DbSession,
    match: ExportableMatch,
    user: Annotated[User, Depends(require_user)],
) -> StreamingResponse:
    return await export_match_json(db, match, viewer=_viewer(user))
