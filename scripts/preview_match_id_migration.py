#!/usr/bin/env python3
"""Dry-run preview for migration 0018 (game → match id rewrite).

Read-only. Prints the G_xxxx → M_xxxx mapping and the per-table row counts that
migration 0018 will rewrite, then changes NOTHING. Run this against a copy of the
prod DB and review the output before `alembic upgrade head` (data-critical-waves
rule). The mapping/counts here MUST equal what the migration applies, because both
import the same contract from app.engine.match_id_rewrite.

Usage:
    python3 scripts/preview_match_id_migration.py --db hoardhurthelp.db
    python3 scripts/preview_match_id_migration.py --db copy.db --dry-run   # same thing

Exit codes:
    0  legacy G_ ids found and previewed (work to do)
    1  bad usage / DB error
    2  nothing to do (already migrated, or no matches) — prints a notice
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Allow running as `python3 scripts/preview_match_id_migration.py` (file path puts
# scripts/ on sys.path, not the repo root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine.match_id_rewrite import LEGACY_PREFIX, affected_tables, to_match_id  # noqa: E402


def _legacy_ids(conn: sqlite3.Connection) -> list[str]:
    """Legacy match ids on the parent table (pre-migration name: `games`).

    Filter in Python so LIKE/ESCAPE quirks across drivers can't drop a `G_demo`.
    """
    return [
        r[0]
        for r in conn.execute("SELECT id FROM games ORDER BY id")
        if str(r[0]).startswith(LEGACY_PREFIX)
    ]


def preview(db_path: str) -> int:
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as exc:  # pragma: no cover - usage error path
        print(f"ERROR: cannot open {db_path}: {exc}", file=sys.stderr)
        return 1

    with conn:
        has_games = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='games'"
        ).fetchone()
        if not has_games:
            print("NOTICE: no `games` table — DB appears already migrated (matches). Nothing to do.")
            return 2

        legacy = _legacy_ids(conn)
        if not legacy:
            print("NOTICE: no G_-prefixed match ids found. Nothing to rewrite.")
            return 2

        print(f"Migration 0018 preview for: {db_path}")
        print(f"\n{len(legacy)} match id(s) will be rewritten (G_ → M_):")
        for gid in legacy:
            print(f"  {gid}  →  {to_match_id(gid)}")

        print("\nPer-(table, column) rows affected:")
        # Columns are still under their pre-migration names here.
        pre_columns = [("games", "id")] + [
            (t, "game_id") for (t, _c) in affected_tables() if t != "matches"
        ]
        total = 0
        for table, column in pre_columns:
            n = conn.execute(
                f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL "
                f"AND substr({column}, 1, 2) = ?",
                (LEGACY_PREFIX,),
            ).fetchone()[0]
            total += n
            print(f"  {table}.{column}: {n}")
        print(f"  TOTAL value rewrites: {total}")
        print("\nDRY RUN — no changes written. Review, then run `alembic upgrade head`.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Preview migration 0018 (game → match id rewrite).")
    p.add_argument("--db", required=True, help="path to the SQLite DB to inspect (use a copy of prod)")
    p.add_argument("--dry-run", action="store_true", help="no-op flag; the script is always read-only")
    args = p.parse_args(argv)
    return preview(args.db)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
