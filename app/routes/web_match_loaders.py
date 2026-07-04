"""Match-loading dependencies and the game-slug redirect machinery.

The canonical match-load helpers for every route module in this package: the
plain ``load_match_or_404`` / ``load_game_match_or_404`` functions, the
game-scoped FastAPI dependencies (``GameScopedMatch*``), and the
``GameSlugRedirect`` exception plus its handler that fixes a wrong ``{game}``
slug in a match URL.

This module depends only on models and request plumbing; it must never import a
``web_*`` route module, so the routes can depend on it without an import cycle.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import DbSession
from app.models.match import Match


def _match_url(match: Match, suffix: str = "") -> str:
    return f"/games/{match.game}/matches/{match.id}{suffix}"


async def load_match_or_404(db: AsyncSession, match_id: str) -> Match:
    """Load a match by id; raise a bare 404 if it does not exist.

    Canonical match-load helper for every route module in this package
    (game-admin, admin, spectator, and the human-facing ``web_*`` routes).
    """
    match = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if match is None:
        raise HTTPException(404)
    return match


# Underscored alias kept for the existing internal call sites in this package
# (web_join.py, web_play.py, matches_user.py, and this module) that already
# import/reference the private name; new callers should use the public
# ``load_match_or_404`` above.
_load_match_or_404 = load_match_or_404


class GameSlugRedirect(Exception):
    """A game-scoped match URL used the wrong ``{game}`` slug — redirect to fix it.

    Raised by the redirect variant of the match-load dependency so the redirect is
    *raised*, not returned inline (which is what lets the routes drop the old
    ``# type: ignore`` workaround). The registered handler turns this into the
    ``RedirectResponse`` the routes used to build by hand.

    Carries everything the handler needs to reproduce the old target byte-for-byte:

    - ``match`` — the loaded match (its real ``game`` is the corrected slug).
    - ``status_code`` — 301 for GETs, 308 for the join POST (a 308 keeps the
      method so the re-issued request still posts).
    - ``suffix`` — when ``None`` the handler swaps the leading ``/games/{game}/``
      of the request path for ``/games/{real}/`` (the common case, where the path
      tail already equals what the old code passed). When set to a string the
      handler builds the target as ``_match_url(match, suffix)`` instead — used by
      the one site (coach-note) whose old target dropped its own path tail.
    """

    def __init__(
        self,
        match: Match,
        *,
        status_code: int = status.HTTP_301_MOVED_PERMANENTLY,
        suffix: str | None = None,
    ) -> None:
        self.match = match
        self.status_code = status_code
        self.suffix = suffix
        super().__init__(f"slug mismatch for match {match.id}")


def _corrected_game_path(request_path: str, real_game: str) -> str:
    """Swap the leading ``/games/{wrong}/`` of a request path for ``/games/{real}/``.

    Only the first path segment after ``/games/`` is the game slug; everything
    after it (``/matches/{id}/...``) is preserved exactly, so the corrected URL
    keeps the request's own path tail.
    """
    prefix = "/games/"
    rest = request_path[len(prefix):]
    _wrong_slug, _, tail = rest.partition("/")
    return f"{prefix}{real_game}/{tail}"


def game_slug_redirect_response(request: Request, exc: Exception) -> RedirectResponse:
    """Exception handler: turn a ``GameSlugRedirect`` into its ``RedirectResponse``.

    Wired in ``app.main`` via ``add_exception_handler``. Reproduces the exact URL
    and status the per-route preambles used to build inline.

    The ``exc`` param is typed ``Exception`` to match Starlette's handler
    signature (it dispatches handlers keyed by type but types the arg loosely);
    it is always a ``GameSlugRedirect`` because that is the key it is registered
    under. The ``isinstance`` narrows it for the type checker and fails loud if it
    is ever wired to the wrong exception.
    """
    if not isinstance(exc, GameSlugRedirect):
        raise TypeError(
            f"game_slug_redirect_response got {type(exc).__name__}, "
            "expected GameSlugRedirect"
        )
    if exc.suffix is None:
        url = _corrected_game_path(request.url.path, exc.match.game)
    else:
        url = _match_url(exc.match, exc.suffix)
    return RedirectResponse(url=url, status_code=exc.status_code)


def raise_for_game_slug_mismatch(
    match: Match,
    game: str,
    *,
    status_code: int = status.HTTP_301_MOVED_PERMANENTLY,
    suffix: str | None = None,
) -> None:
    """Raise ``GameSlugRedirect`` when ``match`` doesn't belong to ``game``; else no-op.

    The single check the redirect-variant dependency uses. Exposed as a plain call
    for the one route (``join_form``) that must run its sign-in / handle redirects
    *before* the slug check, so it can't take the check as a signature dependency
    (those resolve before the body) without reordering. Same logic, same target —
    just invoked from the body at the right point.
    """
    if match.game != game:
        raise GameSlugRedirect(match, status_code=status_code, suffix=suffix)


def _make_game_scoped_match_loader(
    *,
    status_code: int = status.HTTP_301_MOVED_PERMANENTLY,
    suffix: str | None = None,
) -> Callable[[str, str, AsyncSession], Awaitable[Match]]:
    """Build the redirect-variant match-load dependency for a route family.

    The returned dependency loads the match (404 if missing) and, on a ``{game}``
    slug mismatch, raises ``GameSlugRedirect`` so the handler issues the redirect.
    ``status_code``/``suffix`` are baked in per route family (see ``GameSlugRedirect``).
    """

    async def _load(
        game: Annotated[str, Path()],
        match_id: Annotated[str, Path()],
        db: DbSession,
    ) -> Match:
        match = await _load_match_or_404(db, match_id)
        raise_for_game_slug_mismatch(match, game, status_code=status_code, suffix=suffix)
        return match

    return _load


# Canonical redirect-variant loader for GET pages: load + 301 to the corrected
# ``/games/{real}/...`` URL on a slug mismatch. The corrected path keeps the
# request's own tail (``/analysis`` etc.).
load_game_scoped_match = _make_game_scoped_match_loader()
GameScopedMatch = Annotated[Match, Depends(load_game_scoped_match)]

# The join POST wants a 308 (not 301) so the redirected request keeps its method
# and still posts the join form.
load_game_scoped_match_post = _make_game_scoped_match_loader(
    status_code=status.HTTP_308_PERMANENT_REDIRECT
)
GameScopedMatchPost = Annotated[Match, Depends(load_game_scoped_match_post)]

# The coach-note POST is the one site whose old preamble passed an empty suffix,
# so its 301 target dropped ``/coach-note`` and pointed at the bare viewer URL.
# Bake that exact target in via ``suffix=""``.
load_game_scoped_match_to_viewer = _make_game_scoped_match_loader(suffix="")
GameScopedMatchToViewer = Annotated[Match, Depends(load_game_scoped_match_to_viewer)]


async def load_game_match_or_404(
    db: AsyncSession,
    game: str,
    match_id: str,
    *,
    detail: str | None = None,
) -> Match:
    """Load a match and verify it belongs to ``game``; 404 if missing or mismatched.

    The plain-function (non-dependency) form of the game-scoped load, for callers
    that already hold ``db``/``game``/``match_id`` and want a hard 404 on mismatch
    rather than a redirect (the admin JSON + admin web routes). ``detail`` is the
    404 response body message; ``None`` (the default) is a *bare* 404 (FastAPI's
    default ``"Not Found"`` body), matching the admin callers' historical behavior.
    The FastAPI dependency wrappers are the ``GameScopedMatchOr404*`` types below.
    """
    match = await _load_match_or_404(db, match_id)
    if match.game != game:
        raise HTTPException(404, detail=detail)
    return match


def _make_game_scoped_match_or_404_loader(
    *,
    detail: str | None = None,
) -> Callable[[str, str, AsyncSession], Awaitable[Match]]:
    """Build a 404-variant match-load dependency for a route family.

    The returned dependency loads the match (404 if missing) and, on a ``{game}``
    slug mismatch, raises a hard ``HTTPException(404)`` (no redirect). ``detail`` is
    baked in per route family so each site keeps its exact 404 body: ``None`` for a
    bare 404, or a string for a specific message.
    """

    async def _load(
        game: Annotated[str, Path()],
        match_id: Annotated[str, Path()],
        db: DbSession,
    ) -> Match:
        return await load_game_match_or_404(db, game, match_id, detail=detail)

    return _load


# 404-variant loader for the POST mutation routes. They must reject a wrong slug
# rather than redirect (a POST redirect would replay the body against the corrected
# URL). Both current consumers (start_match_submit, play_join) returned the 404
# body "Match not found." on base, so that detail is baked in here to preserve it.
load_game_scoped_match_or_404 = _make_game_scoped_match_or_404_loader(
    detail="Match not found."
)
GameScopedMatchOr404 = Annotated[Match, Depends(load_game_scoped_match_or_404)]
