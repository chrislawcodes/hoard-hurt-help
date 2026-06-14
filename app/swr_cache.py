"""Stale-while-revalidate in-process cache.

A plain TTL cache makes one unlucky request per TTL window pay the full rebuild
(for the home page that was a ~6s spike every minute), and under load several
requests can hit the expiry at once and stampede the database with duplicate
rebuilds.

`SwrCache` fixes both. `get()` returns a cached value immediately whenever one
exists. If the value is past its TTL it still returns the stale value at once and
kicks off a single background refresh — no caller ever waits for a rebuild. Only
the very first request after start, when nothing is cached yet, builds inline.

Single-flight: at most one background refresh per key runs at a time, so an
expiry under load refreshes once, not once per concurrent request.

Single-process app (one Railway instance), so a module-level instance is the
whole cache.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Hashable
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class SwrCache(Generic[K, V]):
    """Stale-while-revalidate cache keyed by ``K`` holding values of type ``V``."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        # key -> (expires_at_monotonic, value)
        self._store: dict[K, tuple[float, V]] = {}
        self._refreshing: set[K] = set()
        # Hold strong refs to in-flight refresh tasks so they aren't GC'd.
        self._tasks: set[asyncio.Task[None]] = set()

    async def get(self, key: K, build: Callable[[], Awaitable[V]]) -> V:
        """Return the cached value, refreshing in the background once stale.

        ``build`` must be self-contained (open its own DB session etc.) because
        a background refresh runs after the triggering request has ended.
        """
        now = time.monotonic()
        entry = self._store.get(key)
        if entry is None:
            # Cold: nothing to serve yet, so build inline. Happens once per key
            # after a restart.
            value = await build()
            self._store[key] = (now + self._ttl, value)
            return value

        expires_at, value = entry
        if expires_at <= now and key not in self._refreshing:
            self._refreshing.add(key)
            task = asyncio.create_task(self._refresh(key, build))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return value

    async def _refresh(self, key: K, build: Callable[[], Awaitable[V]]) -> None:
        try:
            value = await build()
            self._store[key] = (time.monotonic() + self._ttl, value)
        except Exception:
            # fail-open: advisory background refresh. Keep serving the stale
            # value and let a later request try again — never break a page
            # because a cache refresh failed.
            logger.exception("SWR cache refresh failed for key %r", key)
        finally:
            self._refreshing.discard(key)

    def clear(self) -> None:
        """Drop all cached values. Used by tests for isolation."""
        self._store.clear()
        self._refreshing.clear()

    async def wait_for_refreshes(self) -> None:
        """Await any in-flight background refreshes. For tests and shutdown."""
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
