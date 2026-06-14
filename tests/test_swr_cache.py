"""Tests for the stale-while-revalidate cache.

These pin the behaviour that makes the home page fast for everyone: a cached
value is always returned instantly, a stale value triggers a single background
refresh (the caller never waits), and concurrent stale hits refresh only once.
"""

from __future__ import annotations

import asyncio

from app.swr_cache import SwrCache


def _counter_build():
    """An async build that returns 1, 2, 3, ... on successive calls."""
    state = {"n": 0}

    async def build() -> int:
        state["n"] += 1
        return state["n"]

    return build, state


async def test_cold_builds_inline():
    """First call (nothing cached) builds synchronously and returns it."""
    build, state = _counter_build()
    cache: SwrCache[str, int] = SwrCache(ttl_seconds=60)

    assert await cache.get("k", build) == 1
    assert state["n"] == 1


async def test_fresh_value_is_not_rebuilt():
    """A second call inside the TTL returns the cached value without rebuilding."""
    build, state = _counter_build()
    cache: SwrCache[str, int] = SwrCache(ttl_seconds=60)

    await cache.get("k", build)
    assert await cache.get("k", build) == 1
    assert state["n"] == 1


async def test_stale_serves_old_value_then_refreshes():
    """A stale hit returns the old value at once, then refreshes in the background."""
    build, state = _counter_build()
    cache: SwrCache[str, int] = SwrCache(ttl_seconds=0)  # everything is immediately stale

    assert await cache.get("k", build) == 1  # cold build

    # Stale: returns the old value immediately; the refresh has not run yet.
    assert await cache.get("k", build) == 1
    assert state["n"] == 1

    await cache.wait_for_refreshes()
    assert state["n"] == 2  # the background refresh ran exactly once


async def test_concurrent_stale_hits_refresh_once():
    """Single-flight: many stale hits at once trigger only one refresh."""
    state = {"n": 0}

    async def build() -> int:
        state["n"] += 1
        await asyncio.sleep(0.01)
        return state["n"]

    cache: SwrCache[str, int] = SwrCache(ttl_seconds=0)
    await cache.get("k", build)  # cold build -> n == 1

    results = await asyncio.gather(*[cache.get("k", build) for _ in range(5)])
    assert results == [1, 1, 1, 1, 1]  # all served the stale value

    await cache.wait_for_refreshes()
    assert state["n"] == 2  # cold build + exactly one background refresh


async def test_clear_drops_cached_values():
    """clear() forces the next call back onto the cold (inline build) path."""
    build, state = _counter_build()
    cache: SwrCache[str, int] = SwrCache(ttl_seconds=60)

    await cache.get("k", build)
    cache.clear()
    assert await cache.get("k", build) == 2  # rebuilt inline after clear
    assert state["n"] == 2


async def test_keys_are_independent():
    """Different keys cache separately."""
    build, _ = _counter_build()
    cache: SwrCache[str, int] = SwrCache(ttl_seconds=60)

    a = await cache.get("a", build)
    b = await cache.get("b", build)
    assert a == 1
    assert b == 2
