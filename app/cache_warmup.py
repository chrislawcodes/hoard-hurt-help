"""Warm the front page's stale-while-revalidate caches at startup.

The showcase replay and the leaderboard are both served from
stale-while-revalidate caches (see ``app.swr_cache``). Those caches are empty
after every restart, so without warming, the first visitor after a deploy pays
the full rebuild inline — several seconds — while everyone after gets the cached
copy. We redeploy on every merge, so that cold-cache hit happens often.

Warming pre-builds both caches at startup, with the same cache keys the front
page reads, so a hit is ready before any request arrives. It is advisory only:
if a build fails we log and move on, and the page falls back to its normal
inline rebuild — a warm-up failure must never block or break startup.
"""

from __future__ import annotations

import logging

from app.read_models.leaderboard_cache import load_leaderboard_sections_cached
from app.routes.showcase_replay import load_showcase_replay_cached

logger = logging.getLogger(__name__)


async def warm_homepage_caches() -> None:
    """Pre-build the front page's cached reads so the first visitor isn't slow.

    Each cache is warmed independently so one failing build does not skip the
    other. The keys mirror what ``app.routes.web_front_page.home`` reads: the
    default standard-rating showcase replay and the ``included="all"``
    leaderboard sections.
    """
    builds = (
        ("showcase replay", load_showcase_replay_cached()),
        ("leaderboard", load_leaderboard_sections_cached(included="all")),
    )
    for label, build in builds:
        try:
            await build
        except Exception:
            # fail-open: advisory only. A cold cache just means the first
            # visitor pays the inline rebuild, exactly as before this warm-up.
            logger.exception(
                "front page cache warm-up failed for %s; it will fill on first request",
                label,
            )
