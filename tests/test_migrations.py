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
import sqlite3
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


# --- feature 009: game → match id rewrite (migration 0018) ---------------------

# Production-shaped fixture (data-critical-waves rule): a real prod DB has a
# non-numeric id (G_demo) alongside zero-padded ones, so the rewrite must swap the
# G_ prefix, not assume G_NNNN.
_SEED_0017 = """
INSERT INTO users(id,google_sub,email) VALUES (1,'sub1','a@b.com');
INSERT INTO bots(id,user_id,name,key_lookup,key_hint,status,max_concurrent_games,stall_threshold)
  VALUES (1,1,'b','lk','h','active',3,3);
INSERT INTO games(id,name,state,scheduled_start,game_type) VALUES
  ('G_0016','m16','completed','2026-01-01','hoard-hurt-help'),
  ('G_demo','demo','completed','2026-01-01','hoard-hurt-help');
INSERT INTO players(id,game_id,user_id,bot_id,agent_id) VALUES
  (1,'G_0016',1,1,'A'),(2,'G_demo',1,1,'B');
INSERT INTO turns(id,game_id,round,turn,turn_token,opened_at,deadline_at,phase) VALUES
  (1,'G_0016',1,1,'tk1','2026-01-01','2026-01-01','act');
INSERT INTO turn_submissions(id,turn_id,player_id,action) VALUES (1,1,1,'HOARD');
INSERT INTO request_incidents(id,request_id,method,path,game_id,error_type,error_message,stacktrace)
  VALUES (1,'r1','GET','/x','G_0016','E','m','st');
"""


def _seed_0017(db_path: Path) -> dict[str, int]:
    """Upgrade a fresh DB to 0017, seed prod-shaped G_ rows, return row counts."""
    up = _run_alembic(["upgrade", "0017"], db_path)
    assert up.returncode == 0, f"upgrade 0017 failed:\n{up.stdout}\n{up.stderr}"
    conn = sqlite3.connect(db_path)
    with conn:
        conn.executescript(_SEED_0017)
    counts = {
        t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("games", "players", "turns", "turn_submissions", "request_incidents")
    }
    conn.close()
    return counts


def test_0018_rewrites_ids_and_preserves_data(tmp_path: Path) -> None:
    """0018 rewrites every G_ id to M_, renames the schema, and loses no data."""
    db_path = tmp_path / "rewrite.db"
    before = _seed_0017(db_path)

    up = _run_alembic(["upgrade", "head"], db_path)
    assert up.returncode == 0, f"upgrade head failed:\n{up.stdout}\n{up.stderr}"

    conn = sqlite3.connect(db_path)
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731

    # Schema renamed.
    assert q("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='matches'") == 1
    assert q("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='games'") == 0
    cols = {r[1] for r in conn.execute("PRAGMA table_info(matches)")}
    assert "game" in cols and "game_type" not in cols

    # Ids rewritten; counts preserved; no orphans; no stragglers.
    assert sorted(r[0] for r in conn.execute("SELECT id FROM matches")) == ["M_0016", "M_demo"]
    after = {
        "matches": q("SELECT count(*) FROM matches"),
        "players": q("SELECT count(*) FROM players"),
        "turns": q("SELECT count(*) FROM turns"),
        "turn_submissions": q("SELECT count(*) FROM turn_submissions"),
        "request_incidents": q("SELECT count(*) FROM request_incidents"),
    }
    assert after["matches"] == before["games"]
    for t in ("players", "turns", "turn_submissions", "request_incidents"):
        assert after[t] == before[t], t
    assert q(r"SELECT count(*) FROM matches WHERE id LIKE 'G\_%' ESCAPE '\'") == 0
    assert q("SELECT count(*) FROM players p LEFT JOIN matches m ON p.match_id=m.id WHERE m.id IS NULL") == 0
    assert q("SELECT count(*) FROM turns t LEFT JOIN matches m ON t.match_id=m.id WHERE m.id IS NULL") == 0
    assert sorted(r[0] for r in conn.execute("SELECT match_id FROM players")) == ["M_0016", "M_demo"]
    conn.close()
