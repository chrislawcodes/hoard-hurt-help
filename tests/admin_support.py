"""Shared fixture for the admin test files.

Split across test_admin_access.py, test_admin_turn_timing_report.py,
test_admin_game_creation.py, and test_admin_match_lifecycle.py (formerly one
file, test_admin.py).

``reset_db`` here is a deliberate override of tests/conftest.py's shared
``reset_db``: it also stamps ``settings.admin_emails`` so this suite's
admin-gate tests see a real admin, which the shared fixture doesn't do. Every
admin test file needs this version, not the conftest one -- import it here
rather than redefining it per file.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.models import Base

__all__ = ["reset_db"]


# Bespoke: also seeds admin_emails for this file's admin-gate tests, so it can't
# delegate to tests/conftest.py's shared reset_db.
@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)
    monkeypatch.setattr(settings, "admin_emails", "admin@test.com")

    yield test_factory
    await test_engine.dispose()
