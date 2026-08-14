Review this spec artifact using a completeness-adversarial lens.
Stay scoped to that lens.
Trace every consumer and render path of each value the artifact changes — every place that reads, displays, stores, or recomputes it (server code, templates, client scripts, JSON payloads, docs, tests). Confirm each one is updated or explicitly out of scope. Report any consumer the change misses as a finding; a value changed in one place and stale in another is exactly the defect this lens exists to catch.
Approach the artifact adversarially: look for hidden flaws, omitted cases, and weak assumptions before giving credit.
Code context files are provided above. Before asserting any finding, check whether it is confirmed or refuted by the provided code. Each finding must include an evidence tag:
  [CODE-CONFIRMED] — the code directly supports this finding
  [CODE-REFUTED] — the code contradicts this finding (do not include as a finding)
  [UNVERIFIED] — relevant code was not provided; treat as lower confidence
Only assign HIGH severity to CODE-CONFIRMED findings.
The full review artifact text is included below in this prompt.
Output length is limited and a response can be cut off before it finishes. Emit the required structured output first — the "## Findings" section, the "## Residual Risks" section, and the fenced findings JSON block — before any exploratory narration or extended analysis. Do not spend your early output investigating the artifact in prose and save the required format for last: a response cut off mid-investigation must still contain parseable findings. Any deeper supporting analysis you want to add can follow after the required sections and JSON block are complete.
Return markdown using exactly these sections:
## Findings
## Residual Risks
Keep the response concrete and ordered by severity.
End your review with exactly one fenced JSON block — the machine-readable findings summary:
```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "<short title>", "detail": "<one-sentence detail>"}]}
```
Severity must be one of: CRITICAL, HIGH, MEDIUM, LOW. Include one entry per finding in your "## Findings" section.
If you found no issues, the block must be the affirmative clean bill exactly: {"reviewed": true, "findings": []}
This JSON block is required, is machine-parsed, and must be the last thing in your response.

Context: user.py
"""User table — one row per Google identity."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enum_types import FlexibleEnumType


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    given_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    family_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        FlexibleEnumType(UserRole, length=16),
        nullable=False,
        default=UserRole.USER,
        server_default=UserRole.USER.value,
    )
    # Public, chosen display name shown as "by @handle" on the leaderboard.
    # `handle` keeps the case the user typed; `handle_key` is its lowercased form
    # and carries the unique index, so uniqueness is case-insensitive while the
    # displayed capitalization is preserved. Both are NULL until the user picks
    # one (required before owning an agent). Google identity stays the auth layer;
    # the handle is display only and never used for authentication.
    handle: Mapped[str | None] = mapped_column(String(20), nullable=True)
    handle_key: Mapped[str | None] = mapped_column(
        String(20), unique=True, index=True, nullable=True
    )
    # When the handle was last set or changed — powers the 30-day change cooldown.
    handle_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


Context: test_migrations.py
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
from alembic.script import ScriptDirectory
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


def test_mcp_connection_migration_adds_marker_and_unique_index(tmp_path: Path) -> None:
    """0032 adds the MCP connection marker; 0036 makes the unique index per-provider."""
    db_path = tmp_path / "mcp_connection.db"

    up = _run_alembic(["upgrade", "head"], db_path)
    assert up.returncode == 0, f"`alembic upgrade head` failed:\n{up.stdout}\n{up.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(connections)").fetchall()]
        assert "mcp_connected_at" in columns
        index_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_connections_mcp_user_provider_live'"
        ).fetchone()
        assert index_sql is not None
        assert "mcp_connected_at IS NOT NULL AND deleted_at IS NULL" in index_sql[0]
        # After 0036 the uniqueness is per (user, provider), not just per user.
        index_cols = [
            row[2]
            for row in conn.execute(
                "PRAGMA index_info(uq_connections_mcp_user_provider_live)"
            ).fetchall()
        ]
        assert index_cols == ["user_id", "provider"]
    finally:
        conn.close()


def test_0036_collapses_legacy_multi_provider_mcp_connection(tmp_path: Path) -> None:
    """A legacy MCP connection that accumulated several providers (provider NULL,
    many enabled rows) must become single-provider, and the new per-(user, provider)
    index must then permit a SEPARATE connection for the other provider."""
    db_path = tmp_path / "split.db"

    up = _run_alembic(["upgrade", "0035"], db_path)
    assert up.returncode == 0, f"`alembic upgrade 0035` failed:\n{up.stdout}\n{up.stderr}"

    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO users (id, google_sub, email) VALUES (1, 'sub-1', 'a@b.com')"
        )
        # Legacy MCP connection: provider NULL, two providers enabled on it.
        conn.execute(
            "INSERT INTO connections "
            "(id, user_id, provider, key_lookup, key_hint, status, "
            " max_concurrent_games, stall_threshold, mode_a_at) "
            "VALUES (10, 1, NULL, 'k10', 'k10x', 'active', 3, 3, '2026-06-01 00:00:00+00')"
        )
        conn.execute(
            "INSERT INTO connection_providers (connection_id, provider, enabled) "
            "VALUES (10, 'claude', 1), (10, 'gemini', 1)"
        )
    conn.close()

    up = _run_alembic(["upgrade", "0036"], db_path)
    assert up.returncode == 0, f"`alembic upgrade 0036` failed:\n{up.stdout}\n{up.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        # The connection is now single-provider (the first, claude); gemini disabled.
        assert conn.execute(
            "SELECT provider FROM connections WHERE id=10"
        ).fetchone() == ("claude",)
        enabled = dict(
            conn.execute(
                "SELECT provider, enabled FROM connection_providers WHERE connection_id=10"
            ).fetchall()
        )
        assert enabled == {"claude": 1, "gemini": 0}

        # The new index allows a SEPARATE live MCP connection for gemini…
        with conn:
            conn.execute(
                "INSERT INTO connections "
                "(id, user_id, provider, key_lookup, key_hint, status, "
                " max_concurrent_games, stall_threshold, mode_a_at) "
                "VALUES (11, 1, 'gemini', 'k11', 'k11x', 'active', 3, 3, "
                "'2026-06-02 00:00:00+00')"
            )
        # …but a duplicate (same user + provider, live) is rejected.
        try:
            with conn:
                conn.execute(
                    "INSERT INTO connections "
                    "(id, user_id, provider, key_lookup, key_hint, status, "
                    " max_concurrent_games, stall_threshold, mode_a_at) "
                    "VALUES (12, 1, 'claude', 'k12', 'k12x', 'active', 3, 3, "
                    "'2026-06-03 00:00:00+00')"
                )
            raise AssertionError("duplicate (user, provider) MCP connection row was allowed")
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()


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
        # Compare against the real head rather than a hardcoded number: the point
        # is "bootstrapping lands at head", and spelling the revision out here made
        # every new migration fail this test for no reason.
        head = ScriptDirectory.from_config(cfg).get_current_head()
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(head,)]
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


# --- feature decay-switch: matches.mutual_help_decay (migration 0047) ---------


def test_0047_adds_mutual_help_decay_default_on(tmp_path: Path) -> None:
    """0047 adds mutual_help_decay to matches, NOT NULL, and the server default
    backfills every pre-existing row to ON (1) — proving existing matches keep
    today's decaying behavior (data-critical-waves: build passing != prod-correct)."""
    db_path = tmp_path / "decay_switch.db"

    up = _run_alembic(["upgrade", "0046"], db_path)
    assert up.returncode == 0, f"upgrade 0046 failed:\n{up.stdout}\n{up.stderr}"

    # A match row created BEFORE the column existed (omits mutual_help_decay).
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO matches(id, name, state, scheduled_start, game)"
            " VALUES ('M_PRE', 'pre', 'active', '2026-01-01', 'hoard-hurt-help')"
        )
    conn.close()

    up = _run_alembic(["upgrade", "0047"], db_path)
    assert up.returncode == 0, f"upgrade 0047 failed:\n{up.stdout}\n{up.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [("0047",)]
        cols = {row[1]: row for row in conn.execute("PRAGMA table_info(matches)")}
        assert "mutual_help_decay" in cols
        assert cols["mutual_help_decay"][3] == 1  # NOT NULL flag
        # The pre-existing row was backfilled to ON by the server default.
        assert conn.execute(
            "SELECT mutual_help_decay FROM matches WHERE id='M_PRE'"
        ).fetchone()[0] == 1
    finally:
        conn.close()

    # Downgrade (batch mode) drops the column cleanly.
    down = _run_alembic(["downgrade", "0046"], db_path)
    assert down.returncode == 0, f"downgrade 0046 failed:\n{down.stdout}\n{down.stderr}"
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(matches)")}
        assert "mutual_help_decay" not in cols
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
    """When the OAuth vars AND a public base_url are set, no error or warning is emitted."""
    import app.main as app_main

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "env_test")
    monkeypatch.setattr(app_main.settings, "google_client_id", "real-client-id")
    monkeypatch.setattr(app_main.settings, "google_client_secret", "real-client-secret")
    # MCP OAuth discovery also requires a real public base_url in a deployment.
    monkeypatch.setattr(app_main.settings, "base_url", "https://play.example.com")
    # A real deployment also requires a stable MCP signing key.
    monkeypatch.setattr(
        app_main.settings, "mcp_jwt_signing_key", "a-stable-mcp-signing-key-0123456789"
    )

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


def test_0048_maps_each_old_decay_value_to_its_exact_rule(tmp_path: Path) -> None:
    """Migrating must preserve the rule each finished match was played under.

    These rows are experiment results. Stamping every row "decay" — which is what
    adding the column NOT NULL with a server default would do — would silently
    relabel the decay-OFF arm as decay-ON. That corrupts a comparison rather than
    breaking anything visible, so it is pinned here.
    """
    db_path = tmp_path / "modes.db"

    up = _run_alembic(["upgrade", "0047"], db_path)
    assert up.returncode == 0, f"upgrade 0047 failed:\n{up.stdout}\n{up.stderr}"

    conn = sqlite3.connect(db_path)
    with conn:
        for match_id, decayed in (("M_ON", 1), ("M_OFF", 0)):
            conn.execute(
                "INSERT INTO matches(id, name, state, scheduled_start, game,"
                " mutual_help_decay) VALUES (?, ?, 'completed', '2026-01-01',"
                " 'hoard-hurt-help', ?)",
                (match_id, match_id, decayed),
            )
    conn.close()

    up = _run_alembic(["upgrade", "0048"], db_path)
    assert up.returncode == 0, f"upgrade 0048 failed:\n{up.stdout}\n{up.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        rows = dict(conn.execute("SELECT id, mutual_help_mode FROM matches").fetchall())
        cols = {row[1] for row in conn.execute("PRAGMA table_info(matches)")}
    finally:
        conn.close()
    # ON kept its decay, OFF kept its flat payout — neither was relabelled.
    assert rows == {"M_ON": "decay", "M_OFF": "flat_8"}
    assert "mutual_help_decay" not in cols  # the old column is gone


def test_0050_adds_agents_blurb(tmp_path: Path) -> None:
    """0050 adds the nullable agents.blurb column.

    The app test suite builds its schema with ``Base.metadata.create_all``, so it
    never exercises the migration chain — a model/migration mismatch on this
    column is invisible everywhere except here.
    """
    db_path = tmp_path / "agent_blurb.db"

    up = _run_alembic(["upgrade", "0049"], db_path)
    assert up.returncode == 0, f"upgrade 0049 failed:\n{up.stdout}\n{up.stderr}"

    up = _run_alembic(["upgrade", "0050"], db_path)
    assert up.returncode == 0, f"upgrade 0050 failed:\n{up.stdout}\n{up.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [("0050",)]
        agent_cols = {row[1] for row in conn.execute("PRAGMA table_info(agents)")}
        assert "blurb" in agent_cols
    finally:
        conn.close()

    # The downgrade needs batch mode on SQLite; prove it actually runs.
    down = _run_alembic(["downgrade", "0049"], db_path)
    assert down.returncode == 0, f"downgrade to 0049 failed:\n{down.stdout}\n{down.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        agent_cols = {row[1] for row in conn.execute("PRAGMA table_info(agents)")}
        assert "blurb" not in agent_cols
    finally:
        conn.close()


Context: auth.py
"""Google OAuth + sign-out routes."""

import logging

from fastapi import APIRouter, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_errors import api_error
from app.auth.google import oauth
from app.auth.session import clear_session, set_session_user
from app.config import settings
from app.deps import DbSession
from app.models.user import User, UserRole
from app.routes.nav_context import resolve_play_setup_state
from app.schemas.auth import GoogleUserInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


async def sync_google_user(db: AsyncSession, userinfo: GoogleUserInfo) -> User:
    """Create the user on first sign-in, or fill in names we didn't have yet.

    given_name/family_name come straight from Google, so we capture them from the
    start rather than backfilling later. Rows created before we stored names get
    filled on the user's next login; a name that's already set is never
    overwritten.
    """
    role = (
        UserRole.ADMIN
        if userinfo.email.lower() in settings.platform_admin_emails_set
        else UserRole.USER
    )
    user = (
        await db.execute(select(User).where(User.google_sub == userinfo.sub))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            google_sub=userinfo.sub,
            email=userinfo.email,
            name=userinfo.name,
            given_name=userinfo.given_name,
            family_name=userinfo.family_name,
            role=role,
        )
        db.add(user)
        await db.flush()
        return user
    if user.email != userinfo.email:
        # users.email is unique; another row could already hold this address
        # (e.g. an orphaned/duplicate row). google_sub is the real identity key,
        # so on collision keep the stored email and log rather than raise. Role
        # only changes for the platform-admin floor below, so an in-app role
        # promotion is preserved unless the email itself is a floor admin.
        clash = (
            await db.execute(
                select(User.id).where(
                    User.email == userinfo.email, User.id != user.id
                )
            )
        ).scalar_one_or_none()
        if clash is None:
            user.email = userinfo.email
        else:
            logger.warning(
                "skipping email refresh for user %s: %s already in use by user %s",
                user.id,
                userinfo.email,
                clash,
            )
    if user.given_name is None and userinfo.given_name is not None:
        user.given_name = userinfo.given_name
    if user.family_name is None and userinfo.family_name is not None:
        user.family_name = userinfo.family_name
    if userinfo.email.lower() in settings.platform_admin_emails_set:
        user.role = UserRole.ADMIN
    return user


@router.get("/google/login")
async def google_login(request: Request, next: str = "/"):
    request.session["next_after_login"] = next
    # Prefer the explicitly-configured redirect URI (GOOGLE_REDIRECT_URI) so the
    # callback is correct behind a TLS-terminating proxy; fall back to url_for.
    redirect_uri = settings.google_redirect_uri or str(request.url_for("google_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback", name="google_callback")
async def google_callback(request: Request, db: DbSession):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="GOOGLE_AUTH_FAILED",
            message=str(exc),
        ) from exc

    userinfo_raw = token.get("userinfo") or await oauth.google.userinfo(token=token)
    userinfo = GoogleUserInfo(**dict(userinfo_raw))

    user = await sync_google_user(db, userinfo)
    await db.commit()

    set_session_user(request, user.id)

    if user.disabled_at is not None:
        request.session.pop("next_after_login", None)
        return RedirectResponse(url="/disabled", status_code=status.HTTP_303_SEE_OTHER)

    next_url = request.session.pop("next_after_login", "/") or "/"
    if next_url == "/":
        next_url = (await resolve_play_setup_state(db, user)).next_url
    return RedirectResponse(url=next_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def logout(request: Request):
    clear_session(request)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


Context: admin_web.py
"""Admin HTML pages — platform-admin only."""

from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select

from app.config import settings
from app.deps import DbSession, require_platform_admin
from app.engine.match_deletion import delete_match
from app.games import known_types
from app.models.admin_audit_log import AdminAuditLog
from app.models.agent import Agent, AgentKind
from app.models.connection import Connection
from app.models.match import Match
from app.models.player import Player
from app.models.request_incident import RequestIncident
from app.models.user import User
from app.read_models.admin_reports import load_turn_timing_report
from app.routes.web_support import _bucket_matches
from app.services.admin_user_actions import (
    demote_user,
    disable_user,
    enable_user,
    promote_user,
    reset_handle,
)
from app.templating import templates  # shared instance with custom filters

router = APIRouter(tags=["admin"])
_USERS_PAGE_SIZE = 50


@router.get("/admin/matches", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
):
    all_games = (
        (await db.execute(select(Match).order_by(Match.scheduled_start.desc()))).scalars().all()
    )
    def _view(g: Match, player_count: int) -> dict:
        return {
            "id": g.id,
            "game": g.game,
            "name": g.name,
            "match_kind": g.match_kind,
            "scheduled_start": g.scheduled_start,
            "min_players": g.min_players,
            "max_players": g.max_players,
            "state": g.state,
            "player_count": player_count,
        }

    active, scheduled, completed = await _bucket_matches(db, all_games, _view)
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "user": user,
            "is_admin": True,
            "active_games": active,
            "scheduled_games": scheduled,
            "completed_games": completed,
            "game_types": known_types(),
        },
    )


@router.get("/admin/reports", response_class=HTMLResponse)
async def admin_reports(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
    start_date: str | None = None,
    end_date: str | None = None,
    tz: str | None = None,
):
    def _parse_timezone(value: str | None) -> ZoneInfo | timezone:
        if value is None or value.strip() == "":
            return timezone.utc
        try:
            return ZoneInfo(value.strip())
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tz must be a valid IANA timezone name.",
            ) from exc

    def _parse_date(value: str | None, field_name: str) -> date | None:
        if value is None or value.strip() == "":
            return None
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} must use YYYY-MM-DD.",
            ) from exc

    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    timezone_obj = _parse_timezone(tz)
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be on or before end_date.",
        )

    completed_after: datetime | None = None
    completed_before: datetime | None = None
    if start is not None:
        completed_after = datetime.combine(start, time.min, tzinfo=timezone_obj).astimezone(
            timezone.utc
        )
    if end is not None:
        # End is inclusive in the UI, so compare strictly before the next local day.
        completed_before = datetime.combine(
            end + timedelta(days=1),
            time.min,
            tzinfo=timezone_obj,
        ).astimezone(timezone.utc)

    report = await load_turn_timing_report(
        db, completed_after=completed_after, completed_before=completed_before
    )
    return templates.TemplateResponse(
        request,
        "admin/reports.html",
        {
            "user": user,
            "is_admin": True,
            "report": report,
            "start_date": start.isoformat() if start else "",
            "end_date": end.isoformat() if end else "",
            "tz": timezone_obj.key if isinstance(timezone_obj, ZoneInfo) else "UTC",
        },
    )


@router.post("/admin/matches/{match_id}/delete")
async def admin_delete_match(
    match_id: Annotated[str, Path()],
    db: DbSession,
    _: Annotated[User, Depends(require_platform_admin)],
):
    if (
        await db.execute(select(Match).where(Match.id == match_id))
    ).scalar_one_or_none() is None:
        raise HTTPException(404, detail=f"Match {match_id} not found.")
    await delete_match(db, match_id)
    return RedirectResponse(url="/admin/matches", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_list(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
    q: str | None = None,
    page: int = 1,
):
    page = max(1, page)
    stmt = select(User).order_by(User.created_at.desc())
    if q and q.strip():
        pattern = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(User.email).like(pattern),
                func.lower(User.handle).like(pattern),
            )
        )
    offset = (page - 1) * _USERS_PAGE_SIZE
    total = (await db.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (await db.execute(stmt.offset(offset).limit(_USERS_PAGE_SIZE))).scalars().all()

    user_ids = [u.id for u in rows]
    agent_counts: dict[int, int] = {}
    if user_ids:
        count_rows = (
            await db.execute(
                select(Agent.user_id, func.count().label("cnt"))
                .where(
                    Agent.user_id.in_(user_ids),
                    Agent.archived_at.is_(None),
                    Agent.kind == AgentKind.AI,
                )
                .group_by(Agent.user_id)
            )
        ).all()
        agent_counts = {uid: cnt for uid, cnt in count_rows}

    return templates.TemplateResponse(
        request,
        "admin/users_list.html",
        {
            "user": user,
            "is_admin": True,
            "rows": rows,
            "agent_counts": agent_counts,
            "q": q or "",
            "page": page,
            "total": total,
            "page_size": _USERS_PAGE_SIZE,
        },
    )


@router.get("/admin/users/{user_id}", response_class=HTMLResponse)
async def admin_user_detail(
    user_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
):
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, detail="User not found.")

    connections = (
        await db.execute(
            select(Connection)
            .where(Connection.user_id == user_id, Connection.deleted_at.is_(None))
            .order_by(Connection.created_at.desc())
        )
    ).scalars().all()

    agents = (
        await db.execute(
            select(Agent)
            .where(
                Agent.user_id == user_id,
                Agent.archived_at.is_(None),
                Agent.kind == AgentKind.AI,
            )
            .order_by(Agent.created_at.desc())
        )
    ).scalars().all()

    recent_matches = (
        await db.execute(
            select(Match)
            .join(Player, Player.match_id == Match.id)
            .where(Player.user_id == user_id)
            .order_by(Match.scheduled_start.desc())
            .limit(20)
            .distinct()
        )
    ).scalars().all()

    audit_log = (
        await db.execute(
            select(AdminAuditLog, User)
            .join(User, User.id == AdminAuditLog.actor_user_id)
            .where(AdminAuditLog.target_user_id == user_id)
            .order_by(AdminAuditLog.created_at.desc())
            .limit(50)
        )
    ).all()
    audit_entries = [{"log": log, "actor": actor} for log, actor in audit_log]

    return templates.TemplateResponse(
        request,
        "admin/user_detail.html",
        {
            "user": user,
            "is_admin": True,
            "target": target,
            "connections": connections,
            "agents": agents,
            "recent_matches": recent_matches,
            "audit_entries": audit_entries,
            "floor_admin": target.email.lower() in settings.platform_admin_emails_set,
        },
    )


@router.get("/admin/handles", response_class=HTMLResponse)
async def admin_handles(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
):
    """List users who have a public handle, so an admin can reset a bad one."""
    rows = (
        (
            await db.execute(
                select(User).where(User.handle.is_not(None)).order_by(User.handle)
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin/handles.html",
        {"user": user, "is_admin": True, "users": rows},
    )


@router.post("/admin/users/{user_id}/handle/reset")
async def admin_reset_handle(
    user_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
):
    """Clear a user's handle. The string is freed immediately; the user picks a
    new one the next time they need it. Identity is keyed on users.id, so all
    leaderboard history is preserved."""
    await reset_handle(db, actor=user, target_id=user_id)
    await db.commit()
    return RedirectResponse(url="/admin/handles", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/users/{user_id}/disable")
async def admin_disable_user(
    user_id: Annotated[int, Path()],
    db: DbSession,
    actor: Annotated[User, Depends(require_platform_admin)],
):
    await disable_user(db, actor=actor, target_id=user_id)
    await db.commit()
    return RedirectResponse(
        url=f"/admin/users/{user_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/admin/users/{user_id}/enable")
async def admin_enable_user(
    user_id: Annotated[int, Path()],
    db: DbSession,
    actor: Annotated[User, Depends(require_platform_admin)],
):
    await enable_user(db, actor=actor, target_id=user_id)
    await db.commit()
    return RedirectResponse(
        url=f"/admin/users/{user_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/admin/users/{user_id}/promote")
async def admin_promote_user(
    user_id: Annotated[int, Path()],
    db: DbSession,
    actor: Annotated[User, Depends(require_platform_admin)],
):
    await promote_user(db, actor=actor, target_id=user_id)
    await db.commit()
    return RedirectResponse(
        url=f"/admin/users/{user_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/admin/users/{user_id}/demote")
async def admin_demote_user(
    user_id: Annotated[int, Path()],
    db: DbSession,
    actor: Annotated[User, Depends(require_platform_admin)],
):
    await demote_user(db, actor=actor, target_id=user_id)
    await db.commit()
    return RedirectResponse(
        url=f"/admin/users/{user_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/admin/incidents", response_class=HTMLResponse)
async def admin_incidents(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
    request_id: str | None = None,
):
    stmt = select(RequestIncident).order_by(RequestIncident.created_at.desc()).limit(200)
    if request_id:
        stmt = stmt.where(RequestIncident.request_id == request_id.strip())
    incidents = (await db.execute(stmt)).scalars().all()
    return templates.TemplateResponse(
        request,
        "admin/incidents.html",
        {
            "user": user,
            "is_admin": True,
            "incidents": incidents,
            "request_id": request_id or "",
        },
    )


@router.get("/admin/incidents/{incident_id}", response_class=HTMLResponse)
async def admin_incident_detail(
    incident_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
):
    incident = (
        await db.execute(select(RequestIncident).where(RequestIncident.id == incident_id))
    ).scalar_one_or_none()
    if incident is None:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request,
        "admin/incident_detail.html",
        {"user": user, "is_admin": True, "incident": incident},
    )


Artifact: spec.md
# Admin Engagement Dashboard + Signup-Source Capture: Spec

**Revision 2** — rewritten after the spec checkpoint. Three reviewers (Codex
feasibility, Claude requirements, Claude completeness) returned 30 findings, 12 of
them HIGH. Every HIGH was verified against the real code before being accepted;
the verification notes are in "Review outcomes" at the end.

**Feature:** one admin-only page (`/admin/engagement`) answering two questions
Chris cannot answer today — *where do new users fall out on the way to actually
playing?* and *which traffic source produced the people who played?* — plus the
first-touch capture that makes the second question answerable at all.

**Delivery path:** full Feature Factory. `silent-risk=yes`, `design-settled=no`,
`completeness-risk=yes`. Adds a DB migration, a model change, and new middleware.

---

## Why (the problem)

1. **Acquisition is unmeasurable.** Chris recruits alpha users from Reddit.
   Nothing in the app records where a signup came from, so "did r/hermesagent
   work?" is unanswerable.

2. **Engagement data exists but is never read.** `users.created_at`,
   `connections.first_connected_at`, `players.joined_at`,
   `turn_submissions.submitted_at` already describe the whole journey. No page
   reads them together.

3. **The suspected leak is invisible.** Connecting an AI needs two steps the AI
   cannot do for itself (click through Google sign-in, restart the CLI). Nothing
   surfaces how many people stall there.

---

## Design decisions

### D1 — First touch is captured in middleware, on the first page view

A visitor lands on `/?utm_source=hermesagent`, reads the page, clicks around, and
*then* signs in. By the time the request reaches `/auth/google/login`
([auth.py:84](../../../app/routes/auth.py)) the UTM parameter is gone. Capturing at
the login route records nothing for anyone who did not sign in on their first
click.

A small middleware records first touch on the **first request of a session** and
never overwrites it. The value rides in the session, so it survives later
navigation and the Google OAuth round trip.

### D2 — Middleware ordering: added BEFORE `SessionMiddleware`

Starlette's `add_middleware` **inserts at position 0**, so the *last* middleware
added is the *outermost* — `app/main.py:259` documents this on the last-added
`CanonicalHostMiddleware`.

`FirstTouchMiddleware` must run **inside** `SessionMiddleware` so `request.session`
is populated. Its `add_middleware` call must therefore appear **before** the
`SessionMiddleware` call at [main.py:240](../../../app/main.py).

Placed after, capture records nothing, every signup shows as "direct", and
nothing raises. Two independent reviewers checked this claim and confirmed it.

### D3 — This sets a cookie for anonymous visitors. That is a deliberate choice.

**Chris's decision, 2026-08-13, overriding the non-goal recorded in discovery.**

Today an anonymous visitor gets **no cookie at all**: nothing writes to
`request.session` until sign-in, and Starlette only emits `Set-Cookie` when the
session is modified. Writing first touch on the first page view means every
visitor now receives a session cookie.

Chris chose this over the cookie-free alternatives because full attribution — the
source surviving any amount of browsing before signup — is the point of the
feature.

Consequences, stated plainly rather than buried:
- This is an analytics cookie on an anonymous visitor. In the EU that generally
  needs consent; it is not "strictly necessary". **A consent banner is not part of
  this feature** and remains an open item for Chris.
- The discovery non-goal "no anonymous visitor-ID cookie" is superseded and has
  been updated in `state.json`.
- The session is a **signed cookie, not server-side storage**, with a hard ~4KB
  browser limit. Every captured field is length-capped (D4) so first touch cannot
  push a session over the limit and silently drop it.

### D4 — Source is derived, stored raw, length-capped

Stored on `users`: `first_utm_source`, `first_utm_medium`, `first_utm_campaign`,
`first_referrer_host`, `first_landing_path`.

Derived label precedence: `utm_source` → else `first_referrer_host` → else
`"direct"`.

- Only the referrer **host**, never the full URL — a full URL can carry personal
  data in its query string.
- Every value truncated **at capture time**, before it enters the session cookie,
  not merely at the DB write. The cookie is a consumer too.
- **NULL is not "direct".** A row with no capture at all renders as
  `"unknown"`, kept separate from a genuine direct visit. Otherwise the biggest
  row in the source table would silently mean "we failed to capture this".

### D5 — Funnel step order follows the product's real gate ladder

**Corrected after review (both Claude lenses, HIGH, code-confirmed).** Revision 1
ordered the funnel connection-before-agent. The product's own ladder in
[nav_context.py](../../../app/routes/nav_context.py) is the opposite:

```
NEEDS_HANDLE = 1  →  NEEDS_AGENT = 2  →  NEEDS_MCP_CONNECTION = 3
```

Because each step counts only people who cleared the step above (D7), the wrong
order would truncate every user who followed the real path — inventing a drop at
the connection step and understating the agent step. Exactly the bug class this
spec exists to prevent.

**Final steps:**

| # | Step | Source of truth |
|---|---|---|
| 1 | Signed up | `users.created_at` in window |
| 2 | Picked a handle | `users.handle_key IS NOT NULL` |
| 3 | Built an agent | any `agents` row, `kind='ai'`, ever |
| 4 | AI connected | any `connections.first_connected_at IS NOT NULL`, ever |
| 5 | Joined a match | any `players` row, ever |
| 6 | Played a turn | a `turn_submissions` row with `was_defaulted = false` |
| 7 | Came back another day | played on 2+ distinct days |

### D6 — "Started AI hookup" is dropped as a step

Revision 1 made it step 3. It is broken two independent ways, both code-confirmed:

1. **The rows are garbage collected.** `gc_pending_connections`
   ([pending_connection_gc.py:15](../../../app/engine/pending_connection_gc.py))
   hard-deletes incomplete `ConnectionSetup` rows after 24 hours, and it runs on
   ordinary page loads. A historical "started and abandoned" count erases itself.
2. **MCP users never create one.** `ConnectionSetup` is written in exactly one
   place — `connections_machine_setup.py:106`. The MCP path builds a `Connection`
   directly ([mcp_connection.py](../../../app/engine/mcp_connection.py)). Every MCP
   user would fail this step and, under strict nesting, vanish from every step
   after it.

The leak it was meant to show survives as the drop between **built an agent** and
**AI connected**, both of which read durable rows.

A separate live counter — *incomplete setups right now* — is shown as a summary
number, explicitly labelled as a 24-hour snapshot, not a historical total.

### D7 — Strict nesting, and "ever did X"

A user counts at step N only if they cleared steps 1..N-1.

Step membership is **ever did X**: archived agents and deleted connections still
count. Filtering on `archived_at IS NULL` is what made the funnel go *up* in the
middle during design (11 users had connected an AI, 13 had joined a match).

**Known limit, accepted:** `delete_match` and `reset_handle` are real deletes, so
a past funnel is not perfectly reproducible after an admin destroys data. Fixing
that needs an append-only event log, which is out of scope. The page carries a
one-line note saying so.

### D8 — Internal users are marked by a stored flag, set at creation

Keying the exclusion off email is unstable: `sync_google_user` **rewrites
`users.email`** on later logins ([auth.py:52–79](../../../app/routes/auth.py)), so a
user could drift in and out of the excluded group between page loads.

**A stored `users.is_internal` boolean**, set at account creation, never
recomputed. Every query filters on that one column.

**Set at creation in all three places a user row is born** — not only the
backfill:

| Site | Rule |
|---|---|
| `auth.py:41` (Google sign-in) | domain rule |
| `bots/seating.py:51` (house bots user) | always `True` |
| `dev_login.py:51` (dev login) | always `True` |

**Why the flag carries the weight.** Filtering bots by `agents.kind != 'ai'` is
necessary but nowhere near sufficient. Measured in the dev DB:

| Account | Agent kind | Player rows |
|---|---|---|
| `ludumlabs@house.local` | **ai** | 264 |
| `ludumlabs_flat_6@house.local` | **ai** | 120 |
| `ludumlabs_no_repeats@house.local` | **ai** | 120 |
| `bots@agentludum.local` | bot | 45 |
| `sims@agentludum.local` | **ai** | 40 |
| `harness-A/B/C@local.test` | **ai** | 8 each |

Only one is marked a bot. The house, sim and harness accounts run agents marked
real AI and are over 500 of 646 player rows. Without the flag the dashboard
measures Chris, not his users.

Backfill seed domains: `agentludum.local`, `house.local`, `local.test`; plus any
`ADMIN`-role user at migration time.

**Correctable, and findable.** The backfill is a guess about today's domains.
So:
- `/admin/users/{id}` gets a toggle. It must render **outside** the
  `{% if not floor_admin %}` block at
  [user_detail.html:16](../../../app/templates/admin/user_detail.html) — floor admins
  are precisely the accounts the backfill flags, and that gate would hide the fix
  button on exactly the rows that need it.
- `/admin/users` gains an "internal" column so a mis-flagged account can be found
  at all.
- `AdminAction` is a closed enum ([admin_audit_log.py:15](../../../app/models/admin_audit_log.py))
  with five values; the toggle adds `mark_internal` (13 chars, fits the 16-char
  column) so the action can be audit-logged like its neighbours.

### D9 — Every count is distinct users, never rows

One user owns several agents and many player rows. Any "how many people" number is
`COUNT(DISTINCT users.id)`. The funnel is safe by construction (D7 uses sets); the
exposed risk is the **source table**, where a naive join would report one busy user
as dozens.

### D10 — "Played a turn" excludes no-shows

When a player misses a deadline the game **writes a submission row for them**
marked `was_defaulted=True` ([scoring.py:196](../../../app/games/hoard_hurt_help/scoring.py)).
In the dev DB that is 4,614 of 20,276 rows — 23%.

"Played a turn" means `was_defaulted = false`. `connections.turns_played` is
**not** used: it is a per-connection counter, not per-user, and the two would
disagree.

### D11 — One timezone for the whole page

The window controls are timezone-aware (copied from `/admin/reports`), so
"came back another day" must use **the same timezone**, not UTC. A US evening
session spans two UTC days and would read as a return visit that never happened.

### D12 — Smoke-test matches excluded

`/admin/reports` already filters matches named with `TEST_NAME_PREFIX`
([admin_reports.py:109](../../../app/read_models/admin_reports.py)). This page uses the
same filter, or the two admin pages report different numbers for the same week.

### D13 — Crawlers need no special handling

Capture only reaches a `users` row on completed Google sign-in. Crawlers never get
that far.

### D14 — No visitor count on this page

Counting anonymous visitors needs a third-party analytics tool — a paid external
service and Chris's decision, not this feature's. The funnel starts at **signed
up = 100%**, with a one-line note saying visitor numbers arrive when analytics is
added.

---

## What we are building

### 1. `app/identity/first_touch.py` — capture

- `FirstTouchMiddleware`: if `session["first_touch"]` is absent, record
  `{utm_source, utm_medium, utm_campaign, referrer_host, landing_path, at}` from
  the query string and `Referer`. Never overwrite.
- Skips non-page requests. **Real prefixes**, verified: `/static`, `/healthz`,
  `/api`, `/mcp`, `/openapi.json`. (Revision 1 listed `/sse`, which does not
  exist — streams live at `/games/{game}/matches/{id}/stream`.)
- Ignores a `Referer` on our own host — internal navigation is not a source.
- Truncates every field at capture time (D3, D4).
- **Advisory only**: wrapped so a failure logs and continues, commented
  `# fail-open: advisory only` per the repo's fail-loud rule.
- `clear_session` ([auth/session.py:25](../../../app/auth/session.py)) currently pops
  only the user key, so first touch would survive a sign-out and be inherited by
  the next signup in a shared browser. It must clear `first_touch` too.

### 2. `app/models/user.py` + migration — storage

Five nullable `String` columns: `first_utm_source(120)`,
`first_utm_medium(120)`, `first_utm_campaign(120)`, `first_referrer_host(255)`,
`first_landing_path(255)`.

Plus `is_internal: bool`, non-null, default `False`, server default `false`.

Additive and reversible. `op.batch_alter_table` for any constraint operation —
SQLite dev DB (see `tests/test_migrations.py`).

**The `is_internal` backfill is the data-critical step.** It must run as an
explicit `UPDATE`, match domain suffixes case-insensitively, and be verified after
deploy by reading back the flagged row count against the known internal accounts
*before* any number on the page is trusted.

### 3. `app/routes/auth.py` + the two MCP callers — write at account creation

`sync_google_user` has **three** callers, not one: `auth.py:106`,
`mcp_server/oauth_auth.py:156`, `mcp_server/connection_identity.py:176`. The MCP
callers create accounts too, and have no web session.

- Signature gains an optional first-touch argument, written **only** on the
  new-user branch (auth.py:41). A returning user's source is never overwritten.
- MCP-created users record source `"mcp"` — a real, distinct label, so an MCP
  signup is never silently counted as direct web traffic.

### 4. `app/read_models/engagement_funnel.py`

Ordered steps per D5, strict nested sets, distinct users, with the drop from the
previous step. Plus the stuck list: each non-returning user with their furthest
step.

Stuck-list display: users with no handle show `email` (admin-only page, so this is
already visible at `/admin/users`); the list is capped at 50 rows with a count of
any remainder.

### 5. `app/read_models/signup_sources.py`

Per derived label: signups, how many played (D10), percentage. Distinct users
(D9). Sorted by signups descending.

### 6. `app/routes/admin_engagement.py` + template

`GET /admin/engagement`, behind `require_platform_admin`, with the same
date-window controls as `/admin/reports`.

**The four summary numbers, named** (revision 1 referenced them without ever
saying what they were):

1. New signups in the window
2. Users who played a turn in the window
3. Turns played in the window (non-defaulted)
4. Incomplete connection setups **right now** — labelled as a 24-hour snapshot (D6)

Each shows a comparison against the immediately preceding window of equal length.
When the window is unbounded (the default), no comparison is shown — "previous
period" is undefined there.

Then: the funnel, the source table, the stuck list.

### 7. `app/templates/base.html`

"Engagement" in the Platform admin submenu next to Match Admin and Reporting
([base.html:95](../../../app/templates/base.html)).

### 8. `app/routes/admin_web.py` + `user_detail.html` + `users_list.html`

The `is_internal` toggle (outside the `floor_admin` gate), the new `AdminAction`
enum value, and an "internal" column on the users list. See D8.

---

## Acceptance criteria

The authoritative list is the discovery checklist in `state.json`, updated in
lockstep with this revision. Summarised:

1. `/admin/engagement` exists, is in the admin menu, 403s for a non-admin.
2. Funnel strictly nested; a test asserts it never increases, including the
   archived-agent case.
3. Steps in gate-ladder order (D5); a test asserts a user who built an agent but
   never connected is counted at "built an agent".
4. Step membership is "ever did X".
5. Bots and internal users excluded everywhere via the stored flag (D8).
6. `is_internal` set at all three user-creation sites, not only the backfill.
7. First touch survives navigation and the OAuth round trip; end-to-end test.
8. First touch cleared on sign-out.
9. MCP-created users record source `"mcp"`, never `"direct"`.
10. "Played a turn" excludes `was_defaulted` rows (D10); test with a no-show.
11. Every people-count is distinct users (D9); test with a multi-agent user.
12. Retention uses the window's timezone, not UTC (D11).
13. Smoke-test matches excluded (D12).
14. Source table: source → signups → played. NULL renders `"unknown"`, not
    `"direct"`.
15. Stuck list labels handle-less users and caps at 50.
16. Funnel starts at signed up = 100%; no visitor count; note explaining why.
17. Migration additive, reversible, batch-mode, existing users NULL source.
18. `/admin/users/{id}` toggles `is_internal`, renders for floor admins, and is
    audit-logged; `/admin/users` shows the flag.
19. Capture is advisory and never breaks a request.

## Non-goals

1. No third-party analytics service.
2. No consent banner — an open item for Chris, created by D3, not solved here.
3. No append-only event log to make deleted data reproducible (D7 limit).
4. No cohort retention grid — noise at 10–20 users.
5. No public-facing changes beyond the cookie now being set.
6. No changes to `/admin/reports`.

---

## Test plan

**Funnel correctness**
- Nested-set invariant: every step count ≤ the one above, on generated data.
- The archived-agent regression case.
- **Gate-ladder order:** a user with an agent and no connection counts at "built
  an agent" and stops there.
- A user who joined a match without connecting does not appear at "AI connected".
- Bots excluded; internal users excluded from summary numbers *and* funnel.
- Distinct users: one user, three agents, twelve player rows → counts once.
- No-shows excluded: a defaulted-only player is not "played a turn".
- Retention across a US evening spanning two UTC days is **not** a return visit.
- Smoke-test matches contribute nothing.
- Empty window renders without divide-by-zero.

**Capture correctness**
- Land with `?utm_source=x&utm_medium=y`, navigate, sign in → source recorded.
- External `Referer`, no UTM → host stored.
- Internal `Referer` → treated as direct.
- Second visit does not overwrite; returning user does not overwrite.
- Over-long values truncated at capture, before the cookie.
- Sign out, sign in as someone else → second user does not inherit the first
  user's source.
- MCP sign-in path records `"mcp"`.
- Capture raising does not fail the page request.

**Flag and migration**
- `is_internal` stays put after `sync_google_user` rewrites the email.
- Backfill flags seed-domain accounts, leaves a gmail.com user alone.
- Bots-user and dev-login users are created internal.
- `alembic upgrade head` then `downgrade` clean on SQLite.
- Toggle moves a user in/out of the funnel and writes an `AdminAuditLog` row.
- Toggle renders for a floor-admin target.

---

## Review outcomes

30 findings across three reviewers. Every HIGH was checked against the code before
being accepted — the reviewers' claims were not taken on trust.

**Accepted and fixed:** mutable-email exclusion (Codex) → D8. Row-vs-user counting
(Codex) → D9. Funnel order vs gate ladder (both Claude lenses) → D5. Setup-table
GC and the MCP path having no setup row → D6. Three `sync_google_user` callers →
build item 3. No-show submissions → D10. Toggle hidden by `floor_admin` → D8.
`is_internal` bypassed at two creation sites → D8. Session-cookie truncation → D3.
`AdminAction` closed enum → D8. Finding a mis-flagged account → D8. First touch
leaking across sign-out → build item 1. UTC-vs-window timezone → D11. Smoke-test
matches → D12. NULL-vs-direct → D4. Nonexistent `/sse` skip path → build item 1.
"Server-side session" wording → D3. Unnamed summary numbers and undefined previous
period → build item 6. Stuck-list label and cap → build item 4.

**Accepted as a documented limit, not fixed:** admin deletes make past funnels
non-reproducible (D7) — an event log is out of scope.

**Escalated to Chris and decided by him:** the anonymous-visitor cookie. He chose
full attribution over the cookie-free options; recorded in D3 with its
consequences, and the superseded non-goal updated in `state.json`.

**Verified as correct, not a finding:** D2's middleware-ordering claim, confirmed
independently by both Claude reviewers against `app/main.py:225–263`.


Return only markdown with exactly these sections:
## Findings
## Residual Risks
Do not include any other sections. After the Residual Risks section, end with the required fenced findings JSON block described above.