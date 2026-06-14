"""Stale-while-revalidate cache for the expensive global leaderboard projection.

`load_leaderboard_sections` recomputes ELO across every completed match on each
call — an O(n^2) pass that grows with every game played. Both the home page (`/`)
and the `/leaderboard` page call it on every request, so on prod it dominated
time-to-first-byte. The standings only need to be fresh to the minute, so we
cache the computed sections and refresh them in the background once stale (see
`app.swr_cache`) — no request waits for the rebuild.

The cache is keyed by the read parameters that change the result
(`rating_mode`, `included`). Returned `LeaderboardSection` / `LeaderboardRow`
objects are frozen dataclasses, so sharing one cached list across requests is
safe: callers slice and filter into new lists and never mutate the cached
objects.
"""

from __future__ import annotations

import app.db as app_db
from app.read_models.leaderboard import LeaderboardSection, load_leaderboard_sections
from app.swr_cache import SwrCache

LEADERBOARD_CACHE_TTL_SECONDS = 60.0

_cache: SwrCache[tuple[str, str], list[LeaderboardSection]] = SwrCache(
    LEADERBOARD_CACHE_TTL_SECONDS
)


async def load_leaderboard_sections_cached(
    *,
    rating_mode: str = "standard",
    included: str = "agents",
) -> list[LeaderboardSection]:
    """Return leaderboard sections, served from a stale-while-revalidate cache.

    Keyed by `(rating_mode, included)`. The build opens its own session so it can
    run as a background refresh after the triggering request has ended.
    """

    async def _build() -> list[LeaderboardSection]:
        async with app_db.SessionLocal() as session:
            return await load_leaderboard_sections(
                session, rating_mode=rating_mode, included=included
            )

    return await _cache.get((rating_mode, included), _build)


def clear_leaderboard_cache() -> None:
    """Drop all cached leaderboard sections. Used by tests for isolation."""
    _cache.clear()


async def wait_for_leaderboard_refreshes() -> None:
    """Await any in-flight background refreshes. For tests and shutdown."""
    await _cache.wait_for_refreshes()
