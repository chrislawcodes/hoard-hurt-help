"""Cached cross-game showcase replay for the marketing front page.

Building the robot-circle replay means loading a finished game's entire timeline
(every turn, message, and action) and rebuilding it in Python — the single most
expensive thing the home page did per request. The showcase game is the same for
every visitor and only changes when a new game finishes, so we cache the built
replay for a short TTL and serve the cached copy in between.

The selection is public-only (admin-only / under-construction games never
showcase) and viewer-independent, so one global cached value serves everyone.
The replay is served from a stale-while-revalidate cache (see `app.swr_cache`):
no request waits for the rebuild — a stale visitor gets the cached copy instantly
while a single background refresh runs. On a cache miss the build scans only the
most-recent completed games, not the whole table.

Single-process app (one Railway instance), so a module-level instance is the
whole cache. The returned replay JSON is an immutable string, safe to share.
"""

from __future__ import annotations

import logging

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as app_db
from app.games import is_admin_only
from app.models.match import GameState, Match
from app.ops_events import log_ops_event
from app.read_models.matches import count_players_by_match
from app.engine.viewer_presentation import _build_rc_data, sample_replay_data
from app.routes.web_support import _agent_counts, _is_showcase
from app.routes.web_viewer import _game_view_context
from app.swr_cache import SwrCache

logger = logging.getLogger(__name__)

SHOWCASE_CACHE_TTL_SECONDS = 60.0
# Only the most-recent completed games can be "the latest showcase", so scanning
# a small recent window is enough to find one — no need to load the whole table.
_SHOWCASE_SCAN_LIMIT = 25

# (rc_game_id, rc_data_json, rc_game_type)
ShowcaseReplay = tuple[str | None, str, str | None]

_cache: SwrCache[str, ShowcaseReplay] = SwrCache(SHOWCASE_CACHE_TTL_SECONDS)
_CACHE_KEY = "home"


def _anonymous_request() -> Request:
    """A minimal anonymous request for the (viewer-independent) replay build.

    The showcase replay is the same for every visitor, and a background refresh
    has no real request to borrow, so the build always runs as anonymous.
    """
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "session": {},
        }
    )


async def load_showcase_replay_cached() -> ShowcaseReplay:
    """Return the showcase replay from a stale-while-revalidate cache.

    The build opens its own session and uses an anonymous request, so it can run
    as a background refresh after the triggering request has ended.
    """

    async def _build() -> ShowcaseReplay:
        async with app_db.SessionLocal() as session:
            return await _build_showcase_replay(_anonymous_request(), session)

    return await _cache.get(_CACHE_KEY, _build)


def clear_showcase_replay_cache() -> None:
    """Drop the cached showcase replay. Used by tests for isolation."""
    _cache.clear()


async def wait_for_showcase_refreshes() -> None:
    """Await any in-flight background refreshes. For tests and shutdown."""
    await _cache.wait_for_refreshes()


async def _build_showcase_replay(request: Request, db: AsyncSession) -> ShowcaseReplay:
    """Pick the most-recent completed public showcase game and build its replay.

    Falls back to the bundled sample replay (so the animation always plays)
    when no showcase game exists or building one hits a DB error.
    """
    recent = (
        (
            await db.execute(
                select(Match)
                .where(Match.state == GameState.COMPLETED)
                .order_by(Match.scheduled_start.desc())
                .limit(_SHOWCASE_SCAN_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    # Admin-only / under-construction games never showcase on the public page.
    # Filter per-match (only on game types that actually have matches) rather
    # than scanning the whole game registry.
    recent = [m for m in recent if not is_admin_only(m.game)]
    if not recent:
        return None, sample_replay_data(), None

    ids = [m.id for m in recent]
    player_counts = await count_players_by_match(db, ids, active_only=True)
    agent_counts = await _agent_counts(db, ids)

    chosen = next(
        (
            m
            for m in recent
            if _is_showcase(
                {
                    "player_count": player_counts.get(m.id, 0),
                    "agent_count": agent_counts.get(m.id, 0),
                    "name": m.name,
                }
            )
        ),
        None,
    )
    if chosen is None:
        return None, sample_replay_data(), None

    try:
        ctx = await _game_view_context(request, db, chosen)
        return chosen.id, _build_rc_data(ctx["scoreboard"], ctx["history"]), chosen.game
    except SQLAlchemyError:
        log_ops_event(
            logger,
            logging.ERROR,
            "replay_fallback",
            f"DB error building robot-circle replay for match {chosen.id};"
            " falling back to sample",
            match_id=chosen.id,
        )
        return None, sample_replay_data(), None
