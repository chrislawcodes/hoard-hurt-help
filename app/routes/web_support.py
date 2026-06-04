"""Shared helpers for human-facing web routes."""

from fastapi import HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.engine.match_id_rewrite import match_id_candidates
from app.games import get as get_game_module
from app.games.base import GameError, GameTheme
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.user import User
from app.read_models.matches import count_players

_GENERAL_NAMES: tuple[str, ...] = (
    "Napoleon", "Hannibal", "Caesar", "Wellington", "Patton",
    "Eisenhower", "Rommel", "Alexander", "Scipio", "Marlborough",
    "Sherman", "Grant", "Montgomery", "Zhukov", "MacArthur",
    "Khalid", "Saladin", "Genghis", "Sun Tzu", "Bolivar",
)


async def _player_count(db, match_id: str) -> int:
    """Active players only — a pulled-out (left) bot frees its seat."""
    return await count_players(db, match_id, active_only=True)


def _is_admin(user: User | None) -> bool:
    return user is not None and user.email.lower() in settings.admin_emails_set


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
    views: list[dict] = []
    for g in games:
        views.append(
            {
                "id": g.id,
                "game_type": g.game,
                "name": g.name,
                "scheduled_start": g.scheduled_start.isoformat(),
                "max_players": g.max_players,
                "player_count": await _player_count(db, g.id),
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


# A finished game named like this is a deploy smoke test, not a real match —
# keep it out of the public front door (featured replay + recent list).
_TEST_NAME_PREFIX = "prod smoke"


def _is_showcase(view: dict) -> bool:
    """Real, watchable game: had a full table and isn't a smoke test."""
    return view["player_count"] >= 3 and not view["name"].strip().lower().startswith(
        _TEST_NAME_PREFIX
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
                "agent_id": p.agent_id,
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
