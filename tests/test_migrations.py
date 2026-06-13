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

from alembic import command
from alembic.config import Config
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

import app.config as app_config
from app.db_bootstrap import detect_legacy_revision, prepare_database_for_upgrade, verify_required_tables

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


def test_startup_bootstraps_legacy_unversioned_schema(tmp_path: Path, monkeypatch) -> None:
    """A legacy DB with schema but no revision must stamp before upgrading.

    Old deployments built their schema from model metadata, so the database can
    already contain the pre-0018 tables with an empty or missing
    ``alembic_version`` table. Startup should stamp that legacy shape and then
    apply the current head
    instead of crashing on revision 0001.
    """
    db_path = tmp_path / "legacy.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    monkeypatch.setattr(app_config, "settings", app_config.Settings(database_url=db_url))

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    command.upgrade(cfg, "0017")

    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute("DELETE FROM alembic_version")
    conn.close()

    assert detect_legacy_revision(db_url) == "0017"

    prepare_database_for_upgrade(cfg, db_url)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [("0031",)]
        assert (
            conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='matches'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='games'"
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


# --- feature unified-connections: schema foundation (migration 0026) ----------


_SEED_0025 = """
INSERT INTO users(id,google_sub,email,handle,handle_key) VALUES
  (1,'sub-1','u1@example.com','user1','user1'),
  (2,'sub-2','u2@example.com','user2','user2');
INSERT INTO connections(
    id,user_id,nickname,provider,key_lookup,prev_key_lookup,key_hint,status,
    paused_at,paused_reason,deleted_at,first_connected_at,last_seen_at,runner_pid,
    max_concurrent_games,stall_threshold,created_at
) VALUES
  (10,1,'Home Mac','claude','lk10',NULL,'abcd','active',NULL,NULL,NULL,
   '2026-06-09T11:55:00+00:00','2026-06-09T11:59:00+00:00',4321,3,3,'2026-06-09T11:00:00+00:00'),
  (11,1,'Old Laptop','openai','lk11',NULL,'efgh','paused',NULL,NULL,'2026-06-09T09:00:00+00:00',
   '2026-06-09T10:55:00+00:00','2026-06-09T10:59:00+00:00',NULL,2,2,'2026-06-09T09:00:00+00:00');
INSERT INTO agent_versions(id,agent_id,version_no,model,strategy_text,created_at,frozen_at) VALUES
  (100,1,1,'claude-haiku-4-5','Play to win.','2026-06-09T11:00:00+00:00',NULL),
  (101,2,1,'gemini-3.1-pro-preview','Play to win.','2026-06-09T11:00:00+00:00',NULL),
  (102,3,1,'gpt-5.4','Play to win.','2026-06-09T11:00:00+00:00',NULL);
INSERT INTO agents(
    id,user_id,connection_id,kind,name,game,current_version_id,status,archived_at,created_at,
    bot_profile_id,bot_profile_name,bot_strategy,bot_truthfulness,bot_trust_model,bot_seed,
    bot_version,bot_fixture_pack
) VALUES
  (1,1,10,'ai','Attached Claude','hoard-hurt-help',100,'active',NULL,'2026-06-09T11:00:00+00:00',
   NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
  (2,1,NULL,'ai','Detached Gemini','hoard-hurt-help',101,'paused',NULL,'2026-06-09T11:00:00+00:00',
   NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
  (3,1,11,'ai','Deleted OpenAI','hoard-hurt-help',102,'active',NULL,'2026-06-09T11:00:00+00:00',
   NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
  (4,2,NULL,'bot','Bot Seat','hoard-hurt-help',NULL,'active',NULL,'2026-06-09T11:00:00+00:00',
   'bot-4','Bot Seat','leader_pressure',80,'even',17,'v1','pack-a');
INSERT INTO matches(
    id,name,game,state,scheduled_start,started_at,completed_at,cancelled_at,min_players,max_players,
    per_turn_deadline_seconds,total_rounds,turns_per_round,current_round,current_turn,rounds_awarded,
    rules_version,winner_player_id,match_kind,created_at
) VALUES
  ('M_active','Active Match','hoard-hurt-help','active','2026-06-09T10:00:00+00:00',
   '2026-06-09T10:05:00+00:00',NULL,NULL,3,20,60,7,7,1,1,0,'v1',NULL,'manual','2026-06-09T10:00:00+00:00'),
  ('M_done','Done Match','hoard-hurt-help','completed','2026-06-08T10:00:00+00:00',
   '2026-06-08T10:05:00+00:00','2026-06-08T10:30:00+00:00',NULL,3,20,60,7,7,7,7,7,'v1',NULL,'manual','2026-06-08T10:00:00+00:00');
INSERT INTO players(
    id,match_id,user_id,agent_id,agent_version_id,seat_name,model_self_report,joined_at,left_at,
    total_round_wins,total_round_score,current_round_score
) VALUES
  (1,'M_active',1,1,100,'Seat A','claude-haiku-4-5','2026-06-09T10:05:00+00:00',NULL,0.0,10,2),
  (2,'M_active',1,2,101,'Seat B','gemini-3.1-pro-preview','2026-06-09T10:05:00+00:00',NULL,0.0,8,1),
  (3,'M_active',1,3,102,'Seat C','gpt-5.4','2026-06-09T10:05:00+00:00',NULL,0.0,6,0),
  (4,'M_done',2,4,NULL,'Bot Seat',NULL,'2026-06-08T10:05:00+00:00',NULL,1.0,15,3);
"""


def _seed_0025_schema(db_path: Path) -> None:
    up = _run_alembic(["upgrade", "0025"], db_path)
    assert up.returncode == 0, f"upgrade 0025 failed:\n{up.stdout}\n{up.stderr}"
    conn = sqlite3.connect(db_path)
    with conn:
        conn.executescript(_SEED_0025)
    conn.close()


def test_0026_unified_connections_backfills_schema(tmp_path: Path) -> None:
    """0026 adds provider coverage tables and backfills existing rows safely."""
    db_path = tmp_path / "unified_connections.db"
    _seed_0025_schema(db_path)

    up = _run_alembic(["upgrade", "0026"], db_path)
    assert up.returncode == 0, f"upgrade 0026 failed:\n{up.stdout}\n{up.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [("0026",)]
        assert conn.execute("SELECT count(*) FROM connection_providers").fetchone()[0] == 2
        provider_rows = conn.execute(
            "SELECT connection_id, provider, enabled, detected FROM connection_providers "
            "ORDER BY connection_id, provider"
        ).fetchall()
        assert provider_rows == [
            (10, "claude", 1, 0),
            (11, "openai", 1, 0),
        ]

        agent_rows = conn.execute(
            "SELECT id, provider, connection_id FROM agents ORDER BY id"
        ).fetchall()
        assert agent_rows == [
            (1, "claude", 10),
            (2, "gemini", None),
            (3, "openai", 11),
            (4, None, None),
        ]

        player_rows = conn.execute(
            "SELECT id, served_by_connection_id, served_pinned_at FROM players ORDER BY id"
        ).fetchall()
        assert player_rows[0][1] == 10
        assert player_rows[1][1] is None
        assert player_rows[2][1] == 11
        assert player_rows[3][1] is None
        assert player_rows[0][2] is not None
        assert player_rows[2][2] is not None

        assert conn.execute("SELECT count(*) FROM agent_versions").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM matches").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM players").fetchone()[0] == 4
    finally:
        conn.close()


# --- feature: drop agents.connection_id (migration 0027) ---------------------


_SEED_0026 = """
INSERT INTO users(id,google_sub,email,handle,handle_key) VALUES
  (1,'sub-1','u1@example.com','user1','user1');
INSERT INTO connections(
    id,user_id,nickname,provider,key_lookup,prev_key_lookup,key_hint,status,
    paused_at,paused_reason,deleted_at,first_connected_at,last_seen_at,runner_pid,
    max_concurrent_games,stall_threshold,created_at
) VALUES
  (10,1,'Home Mac','claude','lk10',NULL,'abcd','active',NULL,NULL,NULL,
   '2026-06-09T11:55:00+00:00','2026-06-09T11:59:00+00:00',4321,3,3,'2026-06-09T11:00:00+00:00');
INSERT INTO connection_providers(
    id,connection_id,provider,enabled,detected,detected_detail,updated_at
) VALUES
  (1,10,'claude',1,0,NULL,'2026-06-09T11:00:00+00:00');
INSERT INTO agent_versions(id,agent_id,version_no,model,strategy_text,created_at,frozen_at) VALUES
  (100,1,1,'claude-haiku-4-5','Play to win.','2026-06-09T11:00:00+00:00',NULL);
INSERT INTO agents(
    id,user_id,connection_id,provider,kind,name,game,current_version_id,status,
    archived_at,created_at,
    bot_profile_id,bot_profile_name,bot_strategy,bot_truthfulness,bot_trust_model,bot_seed,
    bot_version,bot_fixture_pack
) VALUES
  (1,1,10,'claude','ai','Atlas','hoard-hurt-help',100,'active',NULL,'2026-06-09T11:00:00+00:00',
   NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL),
  (2,1,NULL,'claude','ai','Detached','hoard-hurt-help',100,'paused',NULL,'2026-06-09T11:00:00+00:00',
   NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL);
"""


def _seed_0026_schema(db_path: Path) -> None:
    up = _run_alembic(["upgrade", "0026"], db_path)
    assert up.returncode == 0, f"upgrade 0026 failed:\n{up.stdout}\n{up.stderr}"
    conn = sqlite3.connect(db_path)
    with conn:
        conn.executescript(_SEED_0026)
    conn.close()


def test_0027_drops_agents_connection_id(tmp_path: Path) -> None:
    """0027 drops connection_id from agents; column must be absent after upgrade
    and restored on downgrade."""
    db_path = tmp_path / "drop_agent_connection_id.db"
    _seed_0026_schema(db_path)

    up = _run_alembic(["upgrade", "0027"], db_path)
    assert up.returncode == 0, f"upgrade 0027 failed:\n{up.stdout}\n{up.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [("0027",)]
        # connection_id column must be gone.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(agents)")}
        assert "connection_id" not in cols, "connection_id column must be dropped"
        # provider and other columns must still be present.
        assert "provider" in cols
        assert "kind" in cols
        # Data rows must survive.
        rows = conn.execute("SELECT id, provider, kind FROM agents ORDER BY id").fetchall()
        assert rows == [(1, "claude", "ai"), (2, "claude", "ai")]
    finally:
        conn.close()

    # Downgrade must restore the column (data is not restored — that is expected).
    down = _run_alembic(["downgrade", "0026"], db_path)
    assert down.returncode == 0, f"downgrade 0026 failed:\n{down.stdout}\n{down.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [("0026",)]
        cols = {row[1] for row in conn.execute("PRAGMA table_info(agents)")}
        assert "connection_id" in cols, "connection_id column must be restored after downgrade"
    finally:
        conn.close()


# --- feature: user roles and match ownership (migration 0028) -----------------


_SEED_0027 = """
INSERT INTO users(id,google_sub,email) VALUES
  (1,'sub-admin','admin@example.com'),
  (2,'sub-user','user@example.com');
INSERT INTO matches(
    id,name,game,state,scheduled_start,min_players,max_players,
    per_turn_deadline_seconds,total_rounds,turns_per_round,current_round,current_turn,
    rounds_awarded,rules_version,match_kind,created_at
) VALUES
  ('M_seed','Seed Match','hoard-hurt-help','scheduled','2026-06-09T10:00:00+00:00',
   3,20,60,7,7,0,0,0,'v1','manual','2026-06-09T10:00:00+00:00');
"""


def _seed_0027_schema(db_path: Path) -> None:
    up = _run_alembic(["upgrade", "0027"], db_path)
    assert up.returncode == 0, f"upgrade 0027 failed:\n{up.stdout}\n{up.stderr}"
    conn = sqlite3.connect(db_path)
    with conn:
        conn.executescript(_SEED_0027)
    conn.close()


def test_0028_adds_user_roles_and_match_owner_column(
    tmp_path: Path, monkeypatch
) -> None:
    """0028 backfills admin roles, keeps others at user, and adds match ownership."""
    db_path = tmp_path / "user_roles.db"
    _seed_0027_schema(db_path)
    monkeypatch.setenv("PLATFORM_ADMIN_EMAILS", "admin@example.com")

    up = _run_alembic(["upgrade", "0028"], db_path)
    assert up.returncode == 0, f"upgrade 0028 failed:\n{up.stdout}\n{up.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [("0028",)]

        user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        assert "role" in user_cols

        match_cols = {row[1] for row in conn.execute("PRAGMA table_info(matches)")}
        assert "created_by_user_id" in match_cols

        user_rows = conn.execute(
            "SELECT id, email, role FROM users ORDER BY id"
        ).fetchall()
        assert user_rows == [
            (1, "admin@example.com", "admin"),
            (2, "user@example.com", "user"),
        ]

        match_row = conn.execute(
            "SELECT id, created_by_user_id FROM matches WHERE id='M_seed'"
        ).fetchone()
        assert match_row == ("M_seed", None)

        conn.execute(
            "INSERT INTO users(id,google_sub,email) VALUES (3,'sub-fresh','fresh@example.com')"
        )
        assert conn.execute("SELECT role FROM users WHERE id=3").fetchone()[0] == "user"
    finally:
        conn.close()


# --- feature: admin user management (migration 0029) -------------------------


_SEED_0028 = """
INSERT INTO users(id,google_sub,email,role) VALUES
  (1,'sub-admin','admin@example.com','admin'),
  (2,'sub-user','user@example.com','user');
"""


def _seed_0028_schema(db_path: Path) -> None:
    up = _run_alembic(["upgrade", "0028"], db_path)
    assert up.returncode == 0, f"upgrade 0028 failed:\n{up.stdout}\n{up.stderr}"
    conn = sqlite3.connect(db_path)
    with conn:
        conn.executescript(_SEED_0028)
    conn.close()


def test_0029_adds_admin_audit_log_and_disabled_at(tmp_path: Path) -> None:
    """0029 adds disabled_at to users and creates the admin_audit_log table."""
    db_path = tmp_path / "admin_user_management.db"
    _seed_0028_schema(db_path)

    up = _run_alembic(["upgrade", "0029"], db_path)
    assert up.returncode == 0, f"upgrade 0029 failed:\n{up.stdout}\n{up.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [("0029",)]

        user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        assert "disabled_at" in user_cols

        audit_cols = {row[1] for row in conn.execute("PRAGMA table_info(admin_audit_log)")}
        assert {
            "actor_user_id",
            "target_user_id",
            "action",
            "reason",
            "created_at",
        } <= audit_cols

        index_names = {row[1] for row in conn.execute("PRAGMA index_list(admin_audit_log)")}
        assert "ix_admin_audit_log_actor_user_id" in index_names
        assert "ix_admin_audit_log_target_user_id" in index_names

        fk_rows = conn.execute("PRAGMA foreign_key_list(admin_audit_log)").fetchall()
        assert sorted((row[3], row[6]) for row in fk_rows) == [
            ("actor_user_id", "RESTRICT"),
            ("target_user_id", "RESTRICT"),
        ]
    finally:
        conn.close()


# --- feature: sideline coach (migration 0030) --------------------------------


def test_0030_coaching_backfill_compiles_boolean_sql() -> None:
    """0030 must backfill with a real boolean literal on PostgreSQL."""
    matches = sa.table("matches", sa.column("coaching", sa.Boolean()))
    stmt = (
        sa.update(matches)
        .where(sa.or_(matches.c.coaching.is_(None), matches.c.coaching.is_(False)))
        .values(coaching=sa.true())
    )

    compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "coaching=true" in compiled
    assert "coaching = 0" not in compiled


def test_0030_adds_coach_note_and_coaching_flag(tmp_path: Path) -> None:
    """0030 adds coach_note/coach_note_round to players and coaching to matches."""
    db_path = tmp_path / "sideline_coach.db"

    up = _run_alembic(["upgrade", "0029"], db_path)
    assert up.returncode == 0, f"upgrade 0029 failed:\n{up.stdout}\n{up.stderr}"

    up = _run_alembic(["upgrade", "0030"], db_path)
    assert up.returncode == 0, f"upgrade 0030 failed:\n{up.stdout}\n{up.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [("0030",)]

        player_cols = {row[1] for row in conn.execute("PRAGMA table_info(players)")}
        assert "coach_note" in player_cols
        assert "coach_note_round" in player_cols

        match_cols = {row[1] for row in conn.execute("PRAGMA table_info(matches)")}
        assert "coaching" in match_cols
    finally:
        conn.close()


# --- feature: connection usage counters (migration 0031) ---------------------


def test_0031_adds_connection_usage_counters(tmp_path: Path) -> None:
    """0031 adds api_call_count + turns_played to connections, defaulting to 0."""
    db_path = tmp_path / "usage_counters.db"

    up = _run_alembic(["upgrade", "0030"], db_path)
    assert up.returncode == 0, f"upgrade 0030 failed:\n{up.stdout}\n{up.stderr}"

    up = _run_alembic(["upgrade", "0031"], db_path)
    assert up.returncode == 0, f"upgrade 0031 failed:\n{up.stdout}\n{up.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [("0031",)]
        cols = {row[1]: row for row in conn.execute("PRAGMA table_info(connections)")}
        assert "api_call_count" in cols
        assert "turns_played" in cols
        # NOT NULL with a 0 server default on both.
        assert cols["api_call_count"][3] == 1  # notnull flag
        assert cols["turns_played"][3] == 1
    finally:
        conn.close()

    down = _run_alembic(["downgrade", "0030"], db_path)
    assert down.returncode == 0, f"downgrade to 0030 failed:\n{down.stdout}\n{down.stderr}"
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(connections)")}
        assert "api_call_count" not in cols
        assert "turns_played" not in cols
    finally:
        conn.close()


def test_startup_migrations_skip_on_railway(monkeypatch) -> None:
    """Railway pre-deploy migrations should keep the app from repeating them."""
    # Imported lazily: app.main pulls in the route layer, which finishes migrating
    # off the old Bot model in a later slice; the import (and this test) goes green
    # then. Keeping it lazy lets the migration round-trip tests run in the meantime.
    import app.main as app_main

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "env_test")
    assert app_main._should_run_startup_migrations() is False


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

    up = _run_alembic(["upgrade", "0018"], db_path)
    assert up.returncode == 0, f"upgrade 0018 failed:\n{up.stdout}\n{up.stderr}"

    conn = sqlite3.connect(db_path)

    def q(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

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


# --- migration guard (db_bootstrap._cancel_active_games_if_schema_pending) ---


def _seed_active_match(db_path: Path, match_id: str = "M_TEST") -> None:
    """Insert a minimal ACTIVE match into an already-migrated SQLite database."""
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO matches(id, name, state, scheduled_start, game)"
            " VALUES (?, 'test', 'active', '2026-01-01', 'hoard-hurt-help')",
            (match_id,),
        )
    conn.close()


def test_migration_guard_cancels_active_games_when_behind(
    tmp_path: Path, monkeypatch
) -> None:
    """Active games are cancelled before a destructive migration runs — loudly.

    The guard must log at ERROR naming the cancelled match IDs and the reason,
    so the cancellation is never a silent workaround. (We capture the logger call
    directly rather than via caplog: an earlier alembic-driven fileConfig in this
    module can disable propagation for the app logger.)
    """
    db_path = tmp_path / "guard_behind.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    # Bring the DB to one revision before head so there are pending migrations.
    up = _run_alembic(["upgrade", "0023"], db_path)
    assert up.returncode == 0, f"upgrade 0023 failed:\n{up.stdout}\n{up.stderr}"
    _seed_active_match(db_path)

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    import app.db_bootstrap as db_bootstrap

    log_records: list[tuple[int, str]] = []
    monkeypatch.setattr(
        db_bootstrap.logger,
        "log",
        lambda level, msg, *a, **k: log_records.append((level, msg % a if a else msg)),
    )

    db_bootstrap._cancel_active_games_if_schema_pending(cfg, db_url)

    conn = sqlite3.connect(db_path)
    state = conn.execute("SELECT state FROM matches WHERE id='M_TEST'").fetchone()[0]
    conn.close()
    assert state == "cancelled"

    import logging as _logging

    error_logs = [m for lvl, m in log_records if lvl == _logging.ERROR]
    guard_logs = [m for m in error_logs if "M_TEST" in m]
    assert guard_logs, "guard must log cancelled match IDs at ERROR"
    assert "reason=pending_schema_migration" in guard_logs[0]


def test_migration_guard_skips_when_at_head(tmp_path: Path) -> None:
    """Active games are NOT touched when the database is already at head."""
    db_path = tmp_path / "guard_head.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    up = _run_alembic(["upgrade", "head"], db_path)
    assert up.returncode == 0, f"upgrade head failed:\n{up.stdout}\n{up.stderr}"
    _seed_active_match(db_path)

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    from app.db_bootstrap import _cancel_active_games_if_schema_pending

    _cancel_active_games_if_schema_pending(cfg, db_url)

    conn = sqlite3.connect(db_path)
    state = conn.execute("SELECT state FROM matches WHERE id='M_TEST'").fetchone()[0]
    conn.close()
    assert state == "active"


# --- verify_required_tables startup check ---


def test_verify_required_tables_passes_at_head(tmp_path: Path) -> None:
    """verify_required_tables must not raise when all migrations have run."""
    db_path = tmp_path / "verify_ok.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    up = _run_alembic(["upgrade", "head"], db_path)
    assert up.returncode == 0, f"upgrade head failed:\n{up.stdout}\n{up.stderr}"

    # Should complete without raising.
    verify_required_tables(db_url)


def test_verify_required_tables_raises_when_connection_setups_missing(tmp_path: Path) -> None:
    """verify_required_tables must raise RuntimeError when connection_setups is absent.

    This simulates a deployment that ran migrations only up to revision 0023
    (before connection_setups was added in 0024).
    """
    db_path = tmp_path / "verify_missing.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    up = _run_alembic(["upgrade", "0023"], db_path)
    assert up.returncode == 0, f"upgrade 0023 failed:\n{up.stdout}\n{up.stderr}"

    import pytest

    with pytest.raises(RuntimeError, match="connection_setups"):
        verify_required_tables(db_url)


# --- OAuth startup validation (_check_oauth_config) ---


def test_check_oauth_config_raises_on_railway_when_both_missing(monkeypatch) -> None:
    """On Railway, missing both OAuth vars must raise RuntimeError naming them."""
    import pytest

    import app.main as app_main

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "env_test")
    monkeypatch.setattr(
        app_main.settings,
        "google_client_id",
        "",
    )
    monkeypatch.setattr(
        app_main.settings,
        "google_client_secret",
        "",
    )

    with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID"):
        app_main._check_oauth_config()


def test_check_oauth_config_raises_on_railway_when_one_missing(monkeypatch) -> None:
    """On Railway, missing only google_client_secret must raise and name that var."""
    import pytest

    import app.main as app_main

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "env_test")
    monkeypatch.setattr(app_main.settings, "google_client_id", "real-client-id")
    monkeypatch.setattr(app_main.settings, "google_client_secret", "")

    with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_SECRET"):
        app_main._check_oauth_config()


def test_check_oauth_config_warns_in_local_dev_when_missing(monkeypatch) -> None:
    """In local dev (no Railway marker), missing OAuth vars log a WARNING and do not raise.

    We capture the logger call directly rather than via caplog: an earlier
    alembic-driven fileConfig in this module can disable propagation for the app
    logger (same reason the guard test above does the same thing).
    """
    import app.main as app_main

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_ID", raising=False)
    monkeypatch.setattr(app_main.settings, "google_client_id", "")
    monkeypatch.setattr(app_main.settings, "google_client_secret", "")

    warning_messages: list[str] = []
    monkeypatch.setattr(
        app_main.logger,
        "warning",
        lambda msg, *a, **k: warning_messages.append(msg % a if a else msg),
    )

    app_main._check_oauth_config()  # must not raise

    assert any("GOOGLE_CLIENT_ID" in m for m in warning_messages), (
        f"Expected a warning mentioning GOOGLE_CLIENT_ID; got: {warning_messages}"
    )


def test_check_oauth_config_passes_when_both_set(monkeypatch) -> None:
    """When both OAuth vars are set, no error or warning is emitted regardless of environment."""
    import app.main as app_main

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "env_test")
    monkeypatch.setattr(app_main.settings, "google_client_id", "real-client-id")
    monkeypatch.setattr(app_main.settings, "google_client_secret", "real-client-secret")

    # Must not raise.
    app_main._check_oauth_config()


def test_check_oauth_config_skips_under_pytest(monkeypatch) -> None:
    """PYTEST_CURRENT_TEST must suppress the check entirely — no raise, no warning."""
    import app.main as app_main

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_check_oauth_config_skips_under_pytest")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "env_test")
    monkeypatch.setattr(app_main.settings, "google_client_id", "")
    monkeypatch.setattr(app_main.settings, "google_client_secret", "")

    # Must not raise even though we're on Railway with missing credentials.
    app_main._check_oauth_config()


# --- Platform-admin startup warning (_check_platform_admin_config) ---


def test_check_platform_admin_warns_when_empty(monkeypatch) -> None:
    """When platform_admin_emails_set is empty, a WARNING must be logged."""
    import app.main as app_main

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    # Patch underlying string fields so the computed property returns an empty set
    monkeypatch.setattr(app_main.settings, "platform_admin_emails", "")
    monkeypatch.setattr(app_main.settings, "admin_emails", "")

    warning_messages: list[str] = []
    monkeypatch.setattr(
        app_main.logger,
        "warning",
        lambda msg, *a, **k: warning_messages.append(msg % a if a else msg),
    )

    app_main._check_platform_admin_config()

    assert any("PLATFORM_ADMIN_EMAILS" in m for m in warning_messages), (
        f"Expected a warning mentioning PLATFORM_ADMIN_EMAILS; got: {warning_messages}"
    )


def test_check_platform_admin_silent_when_configured(monkeypatch) -> None:
    """When platform_admin_emails_set is non-empty, no warning is logged."""
    import app.main as app_main

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(app_main.settings, "platform_admin_emails", "admin@example.com")
    monkeypatch.setattr(app_main.settings, "admin_emails", "")

    warning_messages: list[str] = []
    monkeypatch.setattr(
        app_main.logger,
        "warning",
        lambda msg, *a, **k: warning_messages.append(msg % a if a else msg),
    )

    app_main._check_platform_admin_config()

    assert not warning_messages, f"Unexpected warnings: {warning_messages}"


def test_check_platform_admin_skips_under_pytest(monkeypatch) -> None:
    """PYTEST_CURRENT_TEST suppresses the check — no warning even with empty set."""
    import app.main as app_main

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "this_test")
    monkeypatch.setattr(app_main.settings, "platform_admin_emails", "")
    monkeypatch.setattr(app_main.settings, "admin_emails", "")

    warning_messages: list[str] = []
    monkeypatch.setattr(
        app_main.logger,
        "warning",
        lambda msg, *a, **k: warning_messages.append(msg % a if a else msg),
    )

    app_main._check_platform_admin_config()

    assert not warning_messages
