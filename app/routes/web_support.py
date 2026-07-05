"""Shared helpers for human-facing web routes.

NOTE: this module is the shared base for the ``web_*`` route modules; it must
never import any of them (no ``web_viewer``/``web_join``/``web_play`` imports), or
it would create an import cycle. Routes depend on this; this depends on nothing
in routes.

The match-loading dependencies and game-slug redirect machinery now live in
``web_match_loaders``, and the read-model-shaped queries in
``app.read_models.matches``; both are re-exported here so existing importers and
monkeypatch paths keep working unchanged.
"""

from collections.abc import Callable, Sequence

from fastapi import HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.engine.match_id_rewrite import match_id_candidates
from app.match_naming import is_smoke_test_match_name
from app.games import get as get_game_module
from app.games.base import GameError, GameTheme
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.user import User, UserRole
from app.read_models.matches import (
    _agent_count,
    _agent_counts,
    _upcoming_views,
    count_players,
    count_players_by_match,
    rank_standings_by_match,
)
from app.routes.web_match_loaders import (
    GameScopedMatch,
    GameScopedMatchOr404,
    GameScopedMatchPost,
    GameScopedMatchToViewer,
    GameSlugRedirect,
    _corrected_game_path,
    _load_match_or_404,
    _make_game_scoped_match_loader,
    _make_game_scoped_match_or_404_loader,
    _match_url,
    game_slug_redirect_response,
    load_game_match_or_404,
    load_game_scoped_match,
    load_game_scoped_match_or_404,
    load_game_scoped_match_post,
    load_game_scoped_match_to_viewer,
    load_match_or_404,
    raise_for_game_slug_mismatch,
)

__all__ = [
    # Defined here.
    "SEAT_NAME_MAX",
    "unique_seat_name",
    "safe_internal_next",
    "require_can_view_game",
    # Re-exported read models (queries moved to app.read_models.matches).
    "_agent_count",
    "_agent_counts",
    "_upcoming_views",
    # Re-exported match loaders + slug-redirect machinery (moved to
    # app.routes.web_match_loaders).
    "GameScopedMatch",
    "GameScopedMatchOr404",
    "GameScopedMatchPost",
    "GameScopedMatchToViewer",
    "GameSlugRedirect",
    "_corrected_game_path",
    "_load_match_or_404",
    "_make_game_scoped_match_loader",
    "_make_game_scoped_match_or_404_loader",
    "_match_url",
    "game_slug_redirect_response",
    "load_game_match_or_404",
    "load_game_scoped_match",
    "load_game_scoped_match_or_404",
    "load_game_scoped_match_post",
    "load_game_scoped_match_to_viewer",
    "load_match_or_404",
    "raise_for_game_slug_mismatch",
]

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


def require_can_view_game(
    user: User | None,
    game: str,
    *,
    detail: str | None = "Game not found.",
) -> None:
    """Raise 404 when ``game`` is admin-only and ``user`` isn't an admin; else no-op.

    The single raising form of ``_can_view_game`` the routes use to hide an
    under-construction game from non-admins (so its existence isn't revealed).
    ``detail`` is the 404 body; the default matches the sites that returned
    "Game not found.". Pass ``detail=None`` for a bare 404.
    """
    if not _can_view_game(user, game):
        raise HTTPException(status_code=404, detail=detail)


def _game_theme(game: Match) -> GameTheme | None:
    """A game's content tint for its pages (lobby, viewer, analysis, join, etc.).

    base.html stamps it on <main data-game>, so the shared chrome is untouched.
    Unknown game types fall back to the platform-neutral look (no tint).
    """
    try:
        return get_game_module(game.game).theme()
    except GameError:
        return None


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

    # Group players by match, preserving every requested id (empty lists included).
    by_match: dict[str, list[dict]] = {mid: [] for mid in match_ids}
    for p in players:
        by_match[p.match_id].append({
            "agent_id": p.seat_name,
            "round_score": p.current_round_score,
            "round_wins": p.total_round_wins,
        })

    return rank_standings_by_match(by_match, limit=limit)
