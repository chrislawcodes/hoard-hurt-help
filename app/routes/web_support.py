"""Shared helpers for human-facing web routes.

NOTE: this module is the shared base for the ``web_*`` route modules; it must
never import any of them (no ``web_viewer``/``web_join``/``web_play`` imports), or
it would create an import cycle. Routes depend on this; this depends on nothing
in routes.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, status
from fastapi.responses import RedirectResponse
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import DbSession
from app.engine.match_id_rewrite import match_id_candidates
from app.match_naming import is_smoke_test_match_name
from app.games import get as get_game_module
from app.games.base import GameError, GameTheme
from app.models.agent import Agent, AgentKind
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.user import User, UserRole
from app.read_models.matches import count_players, count_players_by_match

_GENERAL_NAMES: tuple[str, ...] = (
    "Napoleon", "Hannibal", "Caesar", "Wellington", "Patton",
    "Eisenhower", "Rommel", "Alexander", "Scipio", "Marlborough",
    "Sherman", "Grant", "Montgomery", "Zhukov", "MacArthur",
    "Khalid", "Saladin", "Genghis", "Sun Tzu", "Bolivar",
)

# Public seat names are capped to fit the standings column.
SEAT_NAME_MAX = 40


def unique_seat_name(base: str, existing: set[str]) -> str:
    """Keep an already-normalized seat name unique within a match.

    Returns *base* unchanged if it's free, otherwise appends a ` #2`, ` #3`, …
    suffix (truncating *base* so the result stays within ``SEAT_NAME_MAX``) until
    it finds an unused name. Callers do their own base normalization (agent name
    vs. human display name) before passing it in. Raises ``HTTPException(409)``
    if the whole suffix range is taken.
    """
    if base not in existing:
        return base
    for index in range(2, 100):
        suffix = f" #{index}"
        candidate = f"{base[: SEAT_NAME_MAX - len(suffix)]}{suffix}"
        if candidate not in existing:
            return candidate
    raise HTTPException(status_code=409, detail="Could not allocate a unique seat name.")


def safe_internal_next(raw: str | None) -> str | None:
    """Accept a `?next` value only when it's an internal path; else return None.

    Guards against an open redirect: the value must be a same-site absolute path
    (starts with a single "/"). A "//host" or "/\\host" prefix is a
    protocol-relative URL that browsers treat as external, and anything with a
    scheme ("http:", "javascript:") is external too — all are rejected. Callers
    decide their own fallback when this returns None.
    """
    if not raw:
        return None
    if not raw.startswith("/"):
        return None
    # "//" and "/\" are protocol-relative (external) — reject both.
    if raw.startswith("//") or raw.startswith("/\\"):
        return None
    return raw


async def _player_count(db, match_id: str) -> int:
    """Active players only — a pulled-out (left) bot frees its seat."""
    return await count_players(db, match_id, active_only=True)


async def _agent_count(db, match_id: str) -> int:
    """Count non-SIM (real agent) players for a match."""
    result = await db.scalar(
        select(func.count())
        .select_from(Player)
        .join(Agent, Agent.id == Player.agent_id)
        .where(Player.match_id == match_id, Agent.kind != AgentKind.BOT)
    )
    return int(result or 0)


async def _agent_counts(db, match_ids: Sequence[str]) -> dict[str, int]:
    """Non-SIM (real agent) player counts for many matches in one grouped query.

    Returns a {match_id: count} map; matches with no real agents are absent and
    should be read as 0. Batched form of _agent_count to avoid an N+1 query when
    rendering lists of finished matches.
    """
    if not match_ids:
        return {}
    rows = (
        await db.execute(
            select(Player.match_id, func.count())
            .select_from(Player)
            .join(Agent, Agent.id == Player.agent_id)
            .where(Player.match_id.in_(match_ids), Agent.kind != AgentKind.BOT)
            .group_by(Player.match_id)
        )
    ).all()
    return {match_id: int(count) for match_id, count in rows}


async def _bucket_matches(
    db,
    matches: Sequence[Match],
    view_builder: Callable[[Match, int], dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split matches into (active, scheduled, completed) view buckets.

    Shared by the platform-admin and game-admin dashboards, which apply the same
    state tests but render different view dicts. ``view_builder`` turns one match
    plus its seated-player count into that page's view dict. Seated-player counts
    are fetched for all matches in a single grouped query (``count_players_by_match``)
    instead of one query per match, so the buckets are identical to the old
    per-match counting but without the N+1.
    """
    counts = await count_players_by_match(db, [m.id for m in matches])
    active: list[dict] = []
    scheduled: list[dict] = []
    completed: list[dict] = []
    for m in matches:
        view = view_builder(m, counts.get(m.id, 0))
        if m.state == GameState.ACTIVE:
            active.append(view)
        elif m.state in (GameState.SCHEDULED, GameState.REGISTERING):
            scheduled.append(view)
        else:
            completed.append(view)
    return active, scheduled, completed


def _is_any_admin(user: User | None) -> bool:
    if user is None:
        return False
    email = user.email.lower()
    return user.role == UserRole.ADMIN or (
        email in settings.all_game_admin_emails_set
    )


def _is_game_admin(user: User | None, game: str) -> bool:
    return user is not None and user.email.lower() in settings.game_admin_emails_for(game)


def _can_view_game(user: User | None, game: str) -> bool:
    """Whether this viewer may see a game. Admin-only (under-construction) games
    are hidden from everyone except admins."""
    from app.games import is_admin_only

    return not is_admin_only(game) or _is_any_admin(user)


async def _upcoming_views(db) -> list[dict]:
    """Scheduled/registering games as the lobby's 'Upcoming' cards.

    Shared by the lobby page and the polled `/upcoming` fragment so both render
    the exact same list. Newest scheduled_start first, matching the page order.
    """
    games = (
        (
            await db.execute(
                select(Match)
                .where(Match.state.in_([GameState.SCHEDULED, GameState.REGISTERING]))
                .order_by(Match.scheduled_start.desc())
            )
        )
        .scalars()
        .all()
    )
    # Active-player counts for every upcoming game in one grouped query (matches
    # _player_count's active_only filter), instead of a query per game.
    player_counts = await count_players_by_match(db, [g.id for g in games], active_only=True)
    views: list[dict] = []
    for g in games:
        views.append(
            {
                "id": g.id,
                "game_type": g.game,
                "name": g.name,
                "match_kind": g.match_kind,
                "scheduled_start": g.scheduled_start,
                "max_players": g.max_players,
                "player_count": player_counts.get(g.id, 0),
            }
        )
    return views


def _game_theme(game: Match) -> GameTheme | None:
    """A game's content tint for its pages (lobby, viewer, analysis, join, etc.).

    base.html stamps it on <main data-game>, so the shared chrome is untouched.
    Unknown game types fall back to the platform-neutral look (no tint).
    """
    try:
        return get_game_module(game.game).theme()
    except GameError:
        return None


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


async def _load_owned_player_match_or_404(
    db: AsyncSession,
    player_id: int,
    user_id: int,
    *,
    missing_detail: str | None = None,
) -> tuple[Player, Match]:
    player = (
        await db.execute(
            select(Player).where(Player.id == player_id, Player.user_id == user_id)
        )
    ).scalar_one_or_none()
    if player is None:
        if missing_detail is not None:
            raise HTTPException(404, detail=missing_detail)
        raise HTTPException(404)
    match = await _load_match_or_404(db, player.match_id)
    return player, match


async def _redirect_to_match(
    db,
    legacy_match_id: str,
    *,
    suffix: str = "",
) -> RedirectResponse:
    match = None
    for candidate_match_id in match_id_candidates(legacy_match_id):
        match = (
            await db.execute(select(Match).where(Match.id == candidate_match_id))
        ).scalar_one_or_none()
        if match is not None:
            break
    if match is None:
        raise HTTPException(404)
    return RedirectResponse(url=_match_url(match, suffix), status_code=status.HTTP_301_MOVED_PERMANENTLY)


def _is_showcase(view: dict) -> bool:
    """Real, watchable game: had a full table, at least one real agent, and isn't a smoke test."""
    return (
        view["player_count"] >= 3
        and view.get("agent_count", 0) >= 1
        and not is_smoke_test_match_name(view["name"])
    )


async def _top_standings(db, match_id: str, limit: int = 3) -> list[dict]:
    """Top-N active players by round-wins then round-score, ranked from 1."""
    players = (
        (
            await db.execute(
                select(Player).where(Player.match_id == match_id, Player.left_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    rows = sorted(
        (
            {
                "agent_id": p.seat_name,
                "round_score": p.current_round_score,
                "round_wins": p.total_round_wins,
            }
            for p in players
        ),
        key=lambda r: (-r["round_wins"], -r["round_score"]),
    )[:limit]
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


async def _batch_top_standings(
    db, match_ids: list[str], limit: int = 3
) -> dict[str, list[dict]]:
    """Fetch top-N standings for multiple matches in one query.

    Returns a dict keyed by match_id, each value is the top-N players sorted by
    round-wins then round-score. Reduces N+1 queries on active games to one.
    """
    if not match_ids:
        return {}

    players = (
        (
            await db.execute(
                select(Player).where(
                    Player.match_id.in_(match_ids),
                    Player.left_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )

    # Group players by match, sort within each group, take top N.
    by_match: dict[str, list[dict]] = {mid: [] for mid in match_ids}
    for p in players:
        by_match[p.match_id].append({
            "agent_id": p.seat_name,
            "round_score": p.current_round_score,
            "round_wins": p.total_round_wins,
        })

    result = {}
    for match_id, player_list in by_match.items():
        sorted_rows = sorted(
            player_list,
            key=lambda r: (-r["round_wins"], -r["round_score"]),
        )[:limit]
        for i, row in enumerate(sorted_rows, start=1):
            row["rank"] = i
        result[match_id] = sorted_rows

    return result
