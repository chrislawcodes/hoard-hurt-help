"""In-process pub/sub for SSE fanout.

One queue per subscriber. `publish` writes to every queue for a game.
`subscribe` returns an async iterator over events for a game.

Good enough for v1 single-instance Railway deploy. If we ever scale
to multiple workers, swap this for Redis Pub/Sub.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class _Subscriber:
    queue: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(maxsize=64))


_subscribers: dict[str, list[_Subscriber]] = {}


async def publish(match_id: str, event_type: str, payload: dict) -> None:
    """Push an SSE-formatted event to every subscriber for this game."""
    msg = f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
    for sub in _subscribers.get(match_id, []):
        # Drop if subscriber is too slow rather than block resolution.
        if sub.queue.full():
            try:
                sub.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        await sub.queue.put(msg)


async def subscribe(match_id: str) -> AsyncIterator[str]:
    """Yield SSE-formatted strings as events arrive."""
    sub = _Subscriber()
    _subscribers.setdefault(match_id, []).append(sub)
    try:
        while True:
            yield await sub.queue.get()
    finally:
        _subscribers.get(match_id, []).remove(sub)
        if not _subscribers.get(match_id):
            _subscribers.pop(match_id, None)
