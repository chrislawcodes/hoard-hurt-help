"""Stale-while-revalidate cache for the lobby's finished games list.

`load_lobby_recent_views` builds the finished-match sections (completed, recent,
bots-only, cancelled) by scanning every completed/cancelled game, then sorting
and filtering. This is expensive when many games have been played. The sections
only need to be fresh to the minute, so we cache them and refresh in the
background once stale (see `app.swr_cache`) — no request waits for the rebuild.
"""

from __future__ import annotations

from typing import Any

import app.db as app_db
from app.read_models.lobby_recent_views import load_lobby_recent_views
from app.swr_cache import SwrCache

LOBBY_CACHE_TTL_SECONDS = 60.0

_cache: SwrCache[str, dict[str, list[dict[str, Any]]]] = SwrCache(
    LOBBY_CACHE_TTL_SECONDS
)


async def load_lobby_recent_views_cached() -> dict[str, list[dict[str, Any]]]:
    """Return lobby finished-games sections, served from a stale-while-revalidate cache.

    The build opens its own session so it can run as a background refresh after
    the triggering request has ended.
    """

    async def _build() -> dict[str, list[dict[str, Any]]]:
        async with app_db.SessionLocal() as session:
            return await load_lobby_recent_views(session)

    return await _cache.get("recent_views", _build)


def clear_lobby_cache() -> None:
    """Drop the cached lobby views. Used by tests for isolation."""
    _cache.clear()


async def wait_for_lobby_refreshes() -> None:
    """Await any in-flight background refreshes. For tests and shutdown."""
    await _cache.wait_for_refreshes()
