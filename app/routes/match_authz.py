"""Who may act on one match — the authorization dependencies for the
game-scoped match routes.

Two roles exist on this platform: **user** and **platform admin**
(``users.role``). A plain user manages the matches they created; a platform
admin manages everyone's.

Every dependency here runs the same four steps, and **the order is the security
contract**:

1. ``require_user`` — 401 for anonymous, the disabled-account bounce for a
   disabled one.
2. ``require_can_view_game`` — **404** when ``{game}`` is under construction and
   the caller is not an admin. This runs before anything else so a 403 can never
   confirm that a hidden game exists.
3. Load the match, 404 on a missing id or a slug that belongs to another game.
4. The role or ownership test, 403 on failure.

That is why ``require_platform_admin`` never appears in a signature here.
FastAPI resolves signature sub-dependencies before the function body, so its 403
would fire ahead of step 2 and leak the hidden game. The role check is written
inline, after step 2.
"""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import Depends, Path, status

from app.api_errors import api_error
from app.deps import DbSession, require_user
from app.models.match import Match
from app.models.user import User, UserRole
from app.routes.web_match_loaders import load_game_match_or_404
from app.routes.web_support import require_can_view_game

# The export routes' historical 404 body is FastAPI's bare "Not Found". Passing
# `detail=None` keeps a hidden game's 404 indistinguishable from a missing
# match's — the whole point of hiding it.
_BARE_404 = None


def _deny_not_admin() -> NoReturn:
    raise api_error(
        status_code=status.HTTP_403_FORBIDDEN,
        code="NOT_PLATFORM_ADMIN",
        message="Platform admin access required.",
    )


def _deny_not_owner() -> NoReturn:
    raise api_error(
        status_code=status.HTTP_403_FORBIDDEN,
        code="NOT_MATCH_OWNER",
        message="You can only manage matches you created.",
    )


async def require_platform_admin_for_game(
    game: Annotated[str, Path()],
    user: Annotated[User, Depends(require_user)],
) -> User:
    """Platform admin, for a route scoped to one game but not to one match.

    Used by the per-game dashboard and the strategy prompts page.
    """
    require_can_view_game(user, game, detail=_BARE_404)
    if user.role != UserRole.ADMIN:
        _deny_not_admin()
    return user


async def load_exportable_match(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
) -> Match:
    """Load a match any signed-in player may export.

    No ownership test: exports are open to every signed-in player. What they
    *contain* is narrowed instead — see ``app.read_models.match_export``.
    """
    require_can_view_game(user, game, detail=_BARE_404)
    return await load_game_match_or_404(db, game, match_id)


async def load_owned_or_admin_match(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
) -> Match:
    """Load a match the caller may manage: its creator, or any platform admin.

    A match with no ``created_by_user_id`` (auto-scheduled, or created before
    the column existed) has no owner, so only a platform admin may manage it.
    """
    require_can_view_game(user, game, detail=_BARE_404)
    match = await load_game_match_or_404(db, game, match_id)
    if user.role == UserRole.ADMIN:
        return match
    if match.created_by_user_id is None or match.created_by_user_id != user.id:
        _deny_not_owner()
    return match


async def load_admin_match(
    game: Annotated[str, Path()],
    match_id: Annotated[str, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
) -> Match:
    """Load a match only a platform admin may act on.

    Start and cancel live here. Cancel is an organizer's power (a player deletes
    their own pre-start match instead), and this start path calls ``start_game``
    directly — no seat check, no player floor, no bot fill — unlike the player
    start route, which goes through ``viewer_start_eligibility``.
    """
    require_can_view_game(user, game, detail=_BARE_404)
    match = await load_game_match_or_404(db, game, match_id)
    if user.role != UserRole.ADMIN:
        _deny_not_admin()
    return match


PlatformAdminForGame = Annotated[User, Depends(require_platform_admin_for_game)]
ExportableMatch = Annotated[Match, Depends(load_exportable_match)]
OwnedOrAdminMatch = Annotated[Match, Depends(load_owned_or_admin_match)]
AdminMatch = Annotated[Match, Depends(load_admin_match)]
