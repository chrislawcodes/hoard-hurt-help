"""Short-lived in-process cache for the expensive global leaderboard projection.

`load_leaderboard_sections` recomputes ELO across every completed match on each
call — an O(n^2) pass that grows with every game played. Both the home page (`/`)
and the `/leaderboard` page call it on every request, so on prod it dominated
time-to-first-byte (~4s while static assets stayed at ~35ms). The standings only
need to be fresh to the minute, so we cache the computed sections for a short TTL
and serve the cached copy in between.

The cache is keyed by the read parameters that change the result
(`rating_mode`, `included`). Returned `LeaderboardSection` / `LeaderboardRow`
objects are frozen dataclasses, so sharing one cached list across requests is
safe: callers slice and filter into new lists and never mutate the cached
objects.

This is a single-process app (one Railway instance), so a module-level dict is
the whole cache. On a miss, two concurrent requests may both recompute before the
first stores its result — that is harmless (same answer) and avoids lock
complexity.
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.read_models.leaderboard import LeaderboardSection, load_leaderboard_sections

LEADERBOARD_CACHE_TTL_SECONDS = 60.0

# key -> (expires_at_monotonic, sections)
_cache: dict[tuple[str, str], tuple[float, list[LeaderboardSection]]] = {}


async def load_leaderboard_sections_cached(
    db: AsyncSession,
    *,
    rating_mode: str = "standard",
    included: str = "agents",
    ttl_seconds: float = LEADERBOARD_CACHE_TTL_SECONDS,
) -> list[LeaderboardSection]:
    """Return leaderboard sections, recomputing at most once per `ttl_seconds`.

    Thin TTL cache over `load_leaderboard_sections`, keyed by
    `(rating_mode, included)`. Pass `ttl_seconds=0` to force a fresh computation.
    """
    key = (rating_mode, included)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]
    sections = await load_leaderboard_sections(
        db, rating_mode=rating_mode, included=included
    )
    _cache[key] = (now + ttl_seconds, sections)
    return sections


def clear_leaderboard_cache() -> None:
    """Drop all cached leaderboard sections. Used by tests for isolation."""
    _cache.clear()
