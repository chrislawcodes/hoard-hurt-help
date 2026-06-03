"""The migration-0018 dry-run preview must be read-only and agree with the
applied migration (data-critical-waves: --dry-run counts must match reality)."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "preview_match_id_migration.py"

# Reuse the migration test's prod-shaped seed + alembic helper.
from tests.test_migrations import _seed_0017, _run_alembic  # noqa: E402


def test_preview_is_read_only_and_matches_applied_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "preview.db"
    before_counts = _seed_0017(db_path)

    before_bytes = db_path.read_bytes()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db_path), "--dry-run"],
        capture_output=True,
        text=True,
    )
    after_bytes = db_path.read_bytes()

    # Read-only: byte-for-byte unchanged, exit 0 (legacy ids found).
    assert proc.returncode == 0, proc.stderr
    assert before_bytes == after_bytes, "preview must not modify the DB"

    # Reports the mapping and the per-table counts that the migration will apply.
    out = proc.stdout
    assert "G_0016  →  M_0016" in out
    assert "G_demo  →  M_demo" in out
    assert f"games.id: {before_counts['games']}" in out
    assert f"players.game_id: {before_counts['players']}" in out

    # Now apply for real and confirm the preview's total equals what changed.
    up = _run_alembic(["upgrade", "head"], db_path)
    assert up.returncode == 0, up.stderr
    conn = sqlite3.connect(db_path)
    applied = sum(
        conn.execute(f"SELECT count(*) FROM {t} WHERE {c} LIKE 'M\\_%' ESCAPE '\\'").fetchone()[0]
        for t, c in (("matches", "id"), ("players", "match_id"),
                     ("turns", "match_id"), ("request_incidents", "match_id"))
    )
    conn.close()
    expected_total = (before_counts["games"] + before_counts["players"]
                      + before_counts["turns"] + before_counts["request_incidents"])
    assert applied == expected_total
    assert f"TOTAL value rewrites: {expected_total}" in out


def test_preview_noops_on_already_migrated_db(tmp_path: Path) -> None:
    """After migration there is no `games` table; preview exits 2 with a notice."""
    db_path = tmp_path / "done.db"
    _seed_0017(db_path)
    _run_alembic(["upgrade", "head"], db_path)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "already migrated" in proc.stdout
