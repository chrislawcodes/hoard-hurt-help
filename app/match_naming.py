"""Neutral home for match-name conventions shared across layers.

A finished game whose name starts with this prefix is a deploy smoke test, not
a real match. Several read models and the public front door need to recognise
and exclude these. The constant used to live in ``app.routes.web_support`` and
was imported by ``app.read_models.lobby_recent_views`` — a read model reaching
into a route. It lives here instead so the data layer never depends on a route.
"""

from __future__ import annotations

# A finished game named like this is a deploy smoke test, not a real match —
# keep it out of public-facing views (featured replay, recent list, leaderboard).
TEST_NAME_PREFIX = "prod smoke"


def is_smoke_test_match_name(name: str) -> bool:
    """True when a match name marks it as a deploy smoke test (not a real game)."""
    return name.strip().lower().startswith(TEST_NAME_PREFIX)


def humanize_game_type(slug: str) -> str:
    """Title-case a game-type slug for display (e.g. 'stub-game' -> 'Stub Game')."""
    return slug.replace("-", " ").title()
