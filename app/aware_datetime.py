"""UTC-aware datetime normalization shared across the app."""

from __future__ import annotations

from datetime import datetime, timezone


def ensure_aware(dt: datetime) -> datetime:
    """SQLite drops tz info on read; normalize a naive value to UTC-aware."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
