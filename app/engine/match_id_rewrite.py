"""Shared G_ ↔ M_ id-rewrite logic for feature 009 (game → match rename).

Migration 0018 and scripts/preview_match_id_migration.py both import from here so
the dry-run plan can never drift from what the migration actually applies. The
rewrite is a pure prefix swap on a fixed set of (table, column) pairs.
"""

from __future__ import annotations

LEGACY_PREFIX = "G_"
MATCH_PREFIX = "M_"

# Every (table, column) holding a match id. `matches.id` is the PK; the rest are
# foreign-key / tracing columns that reference it. Confirmed against the models:
# strategy_prompts / turn_submissions / turn_messages link via player_id/turn_id
# and carry no match id, so they are intentionally absent.
MATCH_ID_COLUMNS: list[tuple[str, str]] = [
    ("matches", "id"),
    ("players", "match_id"),
    ("turns", "match_id"),
    ("request_incidents", "match_id"),
]


def affected_tables() -> list[tuple[str, str]]:
    """The (table, column) pairs whose values get rewritten G_ → M_."""
    return list(MATCH_ID_COLUMNS)


def to_match_id(game_id: str) -> str:
    """`G_0016` → `M_0016`. Pass-through for ids that are already M_."""
    if game_id.startswith(LEGACY_PREFIX):
        return MATCH_PREFIX + game_id[len(LEGACY_PREFIX) :]
    return game_id


def to_game_id(match_id: str) -> str:
    """`M_0016` → `G_0016`. Pass-through for ids that are already G_.

    Used by the migration downgrade and by redirect/back-compat paths that need
    to recover the legacy id.
    """
    if match_id.startswith(MATCH_PREFIX):
        return LEGACY_PREFIX + match_id[len(MATCH_PREFIX) :]
    return match_id


def match_id_candidates(match_id: str) -> tuple[str, ...]:
    """All plausible ids for one match, ordered by the caller's preference.

    Useful while the rollout still has a mix of legacy G_ fixtures and migrated
    M_ data. The first entry is always the input as-is; the rewrite variants are
    appended in a stable order with duplicates removed.
    """
    return tuple(dict.fromkeys((match_id, to_match_id(match_id), to_game_id(match_id))))
