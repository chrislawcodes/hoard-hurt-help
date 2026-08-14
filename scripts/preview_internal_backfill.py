#!/usr/bin/env python3
"""Dry-run preview for migration 0053 (flag platform-owned accounts as internal).

Read-only. Prints exactly which accounts migration 0053 will flag, and why, then
changes NOTHING. Run it against a COPY of the production database and read the
output before merging: merging deploys, and railway.json runs
`alembic upgrade head` as a preDeployCommand, so there is no moment in between.

What to check in the output:
  * every account listed really is ours (a house rig, a harness, the bots user,
    or an admin) — a real player listed here would be silently dropped from every
    number on the engagement dashboard
  * no account you expected is missing — production may hold internal accounts on
    a domain this migration's frozen list does not know about

Usage:
    python3 scripts/preview_internal_backfill.py --db copy.db
    python3 scripts/preview_internal_backfill.py --db copy.db --dry-run  # same thing

Exit codes:
    0  previewed successfully (accounts found)
    1  bad usage / DB error
    2  nothing to flag — prints a notice
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The frozen domain list and bots-account key are loaded from the migration file
# itself, so this preview cannot drift from what actually runs. A preview that
# reports different accounts than the migration touches is worse than no preview.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_backfill_0053",
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "0053_backfill_is_internal.py",
)
assert _spec is not None and _spec.loader is not None
_migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migration)

INTERNAL_DOMAINS: tuple[str, ...] = _migration.INTERNAL_DOMAINS
BOTS_USER_SUB: str = _migration.BOTS_USER_SUB


def _reason(email: str | None, google_sub: str | None, role: str | None) -> str | None:
    """Why this account would be flagged, or None if it would not be."""
    lowered = (email or "").lower()
    for domain in INTERNAL_DOMAINS:
        if lowered.endswith(f"@{domain}"):
            return f"domain @{domain}"
    if google_sub == BOTS_USER_SUB:
        return "platform bots account"
    if (role or "").lower() == "admin":
        return "platform admin"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview migration 0053 (flag internal accounts). Read-only."
    )
    parser.add_argument(
        "--db", required=True, help="path to the SQLite DB to inspect (use a COPY of prod)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="no-op flag; this script is always read-only",
    )
    args = parser.parse_args()

    path = Path(args.db)
    if not path.exists():
        print(f"error: no such database: {path}", file=sys.stderr)
        return 1

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT id, email, google_sub, role FROM users ORDER BY id"
        ).fetchall()
    except sqlite3.Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        connection.close()

    flagged = [
        (user_id, email, reason)
        for user_id, email, google_sub, role in rows
        for reason in [_reason(email, google_sub, role)]
        if reason is not None
    ]

    print(f"users in database:        {len(rows)}")
    print(f"would be flagged internal: {len(flagged)}")
    print(f"would remain counted:      {len(rows) - len(flagged)}")

    if not flagged:
        print("\nnothing to flag — check the domain list matches this database")
        return 2

    print("\nflagged accounts:")
    width = max(len(str(email or "")) for _, email, _ in flagged)
    for user_id, email, reason in flagged:
        print(f"  #{user_id:<5} {str(email or ''):<{width}}  ({reason})")

    print(
        "\nRead every line above. An account flagged here disappears from every "
        "number on the engagement dashboard."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
