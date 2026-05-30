"""Migrations must build a fresh SQLite database end to end.

Local dev and the README both run ``python -m alembic upgrade head`` against a
SQLite file. SQLite cannot ALTER a constraint in place, so every constraint
change in a migration has to go through Alembic batch mode (copy-and-move).
The rest of the test suite builds its schema from model metadata
(``Base.metadata.create_all``), so the migration chain itself is never
exercised there. This test runs the real upgrade/downgrade chain against a
throwaway SQLite file so a future bare ``op.drop_constraint`` / ``op.alter_column``
is caught here instead of by a developer staring at a database that won't build.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_alembic(args: list[str], db_path: Path) -> subprocess.CompletedProcess[str]:
    """Run ``alembic <args>`` in a subprocess pointed at a SQLite file.

    The ``DATABASE_URL`` env var overrides any ``.env`` value, and
    ``migrations/env.py`` derives ``sqlalchemy.url`` from it.
    """
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_sqlite_migrations_round_trip(tmp_path: Path) -> None:
    """A fresh SQLite file must migrate to head and back down to base."""
    db_path = tmp_path / "migration_smoke.db"

    up = _run_alembic(["upgrade", "head"], db_path)
    assert up.returncode == 0, f"`alembic upgrade head` failed:\n{up.stdout}\n{up.stderr}"

    down = _run_alembic(["downgrade", "base"], db_path)
    assert down.returncode == 0, (
        f"`alembic downgrade base` failed:\n{down.stdout}\n{down.stderr}"
    )
