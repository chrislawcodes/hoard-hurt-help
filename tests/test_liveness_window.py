"""C6 dedup: the shared liveness-window primitive.

Pins the naive/aware normalization and the inclusive `<=` boundary that all
three former inline sites now share via `within_window`, plus the
`_connection_is_live` PAUSED short-circuit that must NOT be folded into the
window check.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.engine.connection_health_badge import (
    LIVE_WINDOW_SECONDS,
    _connection_is_live,
    within_window,
)
from app.models.connection import ConnectionStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_within_window_none_is_not_live() -> None:
    assert within_window(None, _now(), 120) is False


def test_within_window_inclusive_boundary_with_naive_timestamp() -> None:
    now = _now()
    # Naive timestamp (SQLite drops tz) exactly at the window edge → still live.
    naive_at_edge = (now - timedelta(seconds=120)).replace(tzinfo=None)
    assert within_window(naive_at_edge, now, 120) is True
    # One second past the edge → not live.
    naive_past_edge = (now - timedelta(seconds=121)).replace(tzinfo=None)
    assert within_window(naive_past_edge, now, 120) is False


def test_connection_is_live_paused_short_circuits() -> None:
    now = _now()
    fresh = now - timedelta(seconds=1)
    # PAUSED short-circuits to False even with a fresh last_seen_at.
    paused = SimpleNamespace(status=ConnectionStatus.PAUSED, last_seen_at=fresh)
    assert _connection_is_live(paused, now) is False
    # ACTIVE + fresh → live; ACTIVE + None → not live.
    active = SimpleNamespace(status=ConnectionStatus.ACTIVE, last_seen_at=fresh)
    assert _connection_is_live(active, now) is True
    assert (
        _connection_is_live(
            SimpleNamespace(status=ConnectionStatus.ACTIVE, last_seen_at=None), now
        )
        is False
    )
    # Stale beyond the window → not live.
    stale = SimpleNamespace(
        status=ConnectionStatus.ACTIVE,
        last_seen_at=now - timedelta(seconds=LIVE_WINDOW_SECONDS + 1),
    )
    assert _connection_is_live(stale, now) is False
