#!/usr/bin/env python3
"""Dry-run preview for migration 0054 (reconstruct historical milestones).

Read-only. Runs the migration's own reconstruction queries as SELECTs and prints
what it would write, then changes NOTHING. Run it against a COPY of the
production database and read the output before merging: merging deploys, and
railway.json runs `alembic upgrade head` as a preDeployCommand.

What to check in the output:
  * the counts fall as you go down the list — a step with MORE people than the one
    above it usually means a query is wrong, not that users skipped ahead
  * played_turn is not suspiciously close to joined_match; autopilot rows are
    excluded and should visibly reduce it
  * the internal/real split matches what you expect

The reconstruction is approximate in both directions and the page says so: it
undercounts where the app deleted the evidence, and it would overcount
played_turn if autopilot seats were not excluded.

Usage:
    python3 scripts/preview_milestone_backfill.py --db copy.db
    python3 scripts/preview_milestone_backfill.py --db copy.db --dry-run  # same thing

Exit codes:
    0  previewed successfully
    1  bad usage / DB error
    2  nothing to reconstruct
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
from pathlib import Path

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "0054_backfill_user_milestones.py"
)

# Load the migration's own queries rather than restating them. A preview that
# reports different numbers than the migration writes is worse than no preview.
_spec = importlib.util.spec_from_file_location("_backfill_0054", _MIGRATION_PATH)
assert _spec is not None and _spec.loader is not None
_migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migration)

SOURCES: list[tuple[str, str]] = [
    ("signed_up", _migration._SIGNED_UP),
    ("picked_handle", _migration._PICKED_HANDLE),
    ("set_up_ai_agent", _migration._SET_UP_AI),
    ("set_up_human_play", _migration._SET_UP_HUMAN),
    ("ai_connected", _migration._AI_CONNECTED),
    ("joined_match", _migration._JOINED_MATCH),
    ("played_turn", _migration._PLAYED_TURN),
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview migration 0054 (milestone backfill). Read-only."
    )
    parser.add_argument(
        "--db", required=True, help="path to the SQLite DB to inspect (use a COPY of prod)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="no-op flag; this script is always read-only"
    )
    args = parser.parse_args()

    path = Path(args.db)
    if not path.exists():
        print(f"error: no such database: {path}", file=sys.stderr)
        return 1

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    total = 0
    try:
        print(f"{'milestone':<20}{'all':>8}{'real users':>13}")
        print("-" * 41)
        for name, query in SOURCES:
            rows = connection.execute(query).fetchall()
            total += len(rows)
            user_ids = {row[0] for row in rows}
            if user_ids:
                placeholders = ",".join("?" * len(user_ids))
                real = connection.execute(
                    f"SELECT COUNT(*) FROM users WHERE id IN ({placeholders}) "
                    "AND NOT is_internal",
                    tuple(user_ids),
                ).fetchone()[0]
            else:
                real = 0
            print(f"{name:<20}{len(rows):>8}{real:>13}")
    except sqlite3.Error as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        print(
            "note: run scripts/preview_internal_backfill.py first — this preview "
            "needs the is_internal column, which migration 0052 adds.",
            file=sys.stderr,
        )
        return 1
    finally:
        connection.close()

    if not total:
        print("\nnothing to reconstruct")
        return 2

    print(f"\n{total} milestone rows would be written.")
    print(
        "Counts should fall down the list. A step with MORE people than the one "
        "above it usually means a query is wrong."
    )
    print(
        "The 'real users' column reflects is_internal as it stands in THIS "
        "database. If migration 0053 has not run yet, nothing is flagged and the "
        "two columns will match."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
