#!/usr/bin/env python3
"""Shared local-SQLite bootstrap for the offline analysis/simulation scripts.

baseline_tournament.py, decay_validation_sim.py, export_baseline_dataset.py, and
new_test_game.py each need some subset of: put the repo root on sys.path so
`app.*` resolves, point `DATABASE_URL` at a local SQLite file, create the
schema, and hand back a session factory. This module holds that dance once;
each script calls only the pieces it needs and supplies its own path handling
and per-script wrinkle (drop-first, order-sensitive module reset, read-only
mode, ...) as parameters.

Import-order warning: `app.db` resolves its engine from `app.config.settings`
at MODULE IMPORT TIME (`engine: AsyncEngine = make_engine()` at the top of
app/db.py). Every function here that ends up importing `app.*` does so
lazily, inside the function body, specifically so `DATABASE_URL` can be set
immediately beforehand. Callers must preserve that ordering themselves for
any `app.*` import they do directly: set the env var (via `set_database_url`
or `bootstrap_file_db`) before importing anything under `app`, including
transitively (e.g. `from app.models.match import Match`).

decay_validation_sim.py additionally deletes all `app.*` entries from
`sys.modules` between conditions so each condition gets a fully fresh engine
bound to that condition's DATABASE_URL. That reset must happen *before*
calling `bootstrap_file_db`/`create_schema` again — this module can't do it
for you because it doesn't know when a caller is done with the previous
condition's modules.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def ensure_repo_root_on_path() -> Path:
    """Put the repo root on sys.path (idempotent) so `app.*` imports resolve.

    Scripts are invoked as `python scripts/<name>.py`, so the interpreter only
    puts `scripts/` itself on sys.path by default.
    """
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def set_database_url(db_path: str, *, mkdir: bool = True, override: bool = True) -> None:
    """Point DATABASE_URL at a local SQLite file.

    Must be called before any `app.*` import (see module docstring).

    override=True (default) always sets DATABASE_URL — the behavior
    baseline_tournament.py and decay_validation_sim.py need, since they own
    the whole process and must not silently reuse a stale value.
    override=False only fills it in if unset (`os.environ.setdefault`) — the
    behavior export_baseline_dataset.py needs, since it reads a DB another
    script already created and must not clobber a caller's own DATABASE_URL.
    """
    path = Path(db_path)
    if mkdir:
        path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite+aiosqlite:///{path}"
    if override:
        os.environ["DATABASE_URL"] = url
    else:
        os.environ.setdefault("DATABASE_URL", url)


async def create_schema() -> None:
    """Create all tables on the currently-configured `app.db.engine`.

    Imports `app.db` / `app.models` at call time (not at module load time) so
    a caller that just cleared `app.*` from sys.modules gets a fresh engine
    bound to whatever DATABASE_URL is set right now.
    """
    from app.db import engine
    from app.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def bootstrap_file_db(
    db_path: str,
    *,
    mkdir: bool = True,
    override: bool = True,
    create: bool = True,
) -> async_sessionmaker[AsyncSession]:
    """Point the app at `db_path`, optionally create its schema, return SessionLocal.

    This is the common path for baseline_tournament.py and
    decay_validation_sim.py: set DATABASE_URL, `Base.metadata.create_all`,
    hand back the module-level `app.db.SessionLocal` (bound to the new engine
    because it's imported fresh, after the env var is set).

    Callers with order-sensitive setup of their own (decay_validation_sim.py
    resets sys.modules between conditions) must do that reset BEFORE calling
    this function, not after.
    """
    ensure_repo_root_on_path()
    set_database_url(db_path, mkdir=mkdir, override=override)
    if create:
        await create_schema()

    from app.db import SessionLocal

    return SessionLocal
