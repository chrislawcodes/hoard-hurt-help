"""Tests for the front page cache warm-up run at startup.

Warming pre-builds the showcase replay and leaderboard caches so the first
visitor after a deploy doesn't pay the inline rebuild. These pin two things:
warming populates both caches under the keys the front page reads, and a build
that raises is swallowed (advisory only) so it can never break startup.
"""

from __future__ import annotations


from app import cache_warmup
from app.cache_warmup import warm_homepage_caches
from app.read_models.leaderboard_cache import _cache as leaderboard_cache
from app.routes.showcase_replay import _cache as showcase_cache


async def test_warm_populates_both_caches(reset_db):
    """After warming, both caches hold an entry, so the next read is a hit.

    An empty DB is enough: the showcase build falls back to the bundled sample
    and the leaderboard build returns empty sections — both still cache.
    """
    assert not showcase_cache._store
    assert not leaderboard_cache._store

    await warm_homepage_caches()

    assert "home" in showcase_cache._store
    assert ("standard", "all") in leaderboard_cache._store


async def test_warm_is_fail_open(reset_db, monkeypatch):
    """A build that raises is swallowed — warm-up must never break startup."""

    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(cache_warmup, "load_showcase_replay_cached", boom)
    monkeypatch.setattr(cache_warmup, "load_leaderboard_sections_cached", boom)

    # Must return normally, not propagate the error.
    await warm_homepage_caches()
