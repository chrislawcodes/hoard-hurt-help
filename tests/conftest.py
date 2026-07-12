"""Pytest fixtures shared across the test suite.

- Async event loop config via pytest-asyncio (asyncio_mode = "auto" in pyproject).
- In-memory SQLite engine, fresh per test.
- FastAPI TestClient bound to the in-memory engine.

Canonical home for the fixtures every DB/HTTP test reuses:
``reset_db`` (rebinds the app to a fresh in-memory SQLite), ``db`` (a bare
session for direct-logic tests), ``client`` (an httpx client bound to the app),
plus ``session_cookie`` / ``signed_in_cookies`` for signed-in requests. Tests
that need genuinely different setup (a file-backed DB, extra monkeypatches, no
DB rebind at all) keep their own independent local copy, with a comment
explaining why it can't delegate here. Test files where every test just needs
``reset_db`` to run without asking for it by name (most of them) instead keep a
thin autouse override that delegates to this one:

    @pytest.fixture(autouse=True)
    async def reset_db(reset_db: async_sessionmaker) -> async_sessionmaker:
        return reset_db

This is the standard pytest "override a fixture but still use the original"
pattern (request the overridden name as a parameter); it does not duplicate the
engine/session setup, and the fast-lane tagging below still sees ``reset_db``
in the test's fixturenames either way.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import ModuleType

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import settings
from app.db import make_engine
from app.models import Base

# Re-export the canonical user factory so existing `from tests.conftest import
# make_user` call sites keep working while `tests.factories` stays the single
# home for it (the two definitions were byte-identical).
from tests.factories import make_user

__all__ = [
    "load_script_module",
    "make_user",
    "session_cookie",
    "signed_in_cookies",
]


# Fixtures whose presence means a test boots the in-memory DB or the HTTP
# stack — the slow part of the suite. Tests pulling in any of these are tagged
# `integration`; everything else is `unit` (the fast lane). Kept here, next to
# the fixtures themselves, so the list stays honest as fixtures change.
_INTEGRATION_FIXTURES = frozenset(
    {"engine", "session_factory", "db", "reset_db", "client", "async_client", "ac"}
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-tag every test as `unit` (fast) or `integration` (slow).

    A test that requests a DB engine or an HTTP client (directly or through
    another fixture) is slow, so it is marked `integration`. Everything else is
    marked `unit`. This drives `pytest -m "not integration"` (the fast lane)
    without hand-marking 100+ test files.
    """
    for item in items:
        fixtures = set(getattr(item, "fixturenames", ()))
        marker = "integration" if fixtures & _INTEGRATION_FIXTURES else "unit"
        item.add_marker(getattr(pytest.mark, marker))


@pytest.fixture(scope="session")
def event_loop() -> asyncio.AbstractEventLoop:
    """Session-scoped event loop for async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def _clear_process_caches() -> None:
    """Reset the process-wide read caches before each test.

    These caches are keyed by query params (or a fixed key), not by DB, so
    without this a cached result from one test's in-memory DB would leak into
    the next.
    """
    from app.read_models.leaderboard_cache import clear_leaderboard_cache
    from app.read_models.lobby_cache import clear_lobby_cache
    from app.routes.showcase_replay import clear_showcase_replay_cache

    clear_leaderboard_cache()
    clear_lobby_cache()
    clear_showcase_replay_cache()


@pytest.fixture(autouse=True)
def _restore_game_registry() -> Iterator[None]:
    """Snapshot the game registry and restore it after each test.

    Some tests register stub game modules (e.g. inside a test body). Without
    this, those stubs leak into every later test in the run, causing
    order-dependent failures (a leaked stub without `config_defaults()` makes
    registry helpers raise AttributeError). Restoring the snapshot keeps each
    test's registry changes from escaping. Module-scoped registration fixtures
    register before this runs, so their stubs are part of the snapshot and
    survive until their own teardown.
    """
    import app.games as registry

    snapshot = dict(registry._REGISTRY)
    try:
        yield
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(snapshot)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Fresh in-memory SQLite engine per test."""
    eng = make_engine("sqlite+aiosqlite:///:memory:")
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker:
    """Session factory bound to the in-memory engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def reset_db(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker]:
    """Rebind the production session factory/engine to in-memory SQLite per test.

    Yields the session factory so tests can open their own sessions
    (`async with reset_db() as db: ...`). Both `app.db.SessionLocal` and
    `app.db.engine` are rebound so route handlers hit the in-memory DB.

    NOTE: this is intentionally NOT autouse. The fast-lane tagging in
    `pytest_collection_modifyitems` keys off `reset_db` appearing in a test's
    fixturenames; an autouse version would tag the entire suite `integration`
    and collapse the fast lane. Tests request it explicitly (directly or via
    another fixture).
    """
    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    test_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)
    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def db(
    engine: AsyncEngine, session_factory: async_sessionmaker
) -> AsyncIterator[AsyncSession]:
    """A bare in-memory session for direct-logic tests.

    Creates the schema on the fresh `engine` and yields one open session. This
    does NOT rebind `app.db` — use `reset_db` for tests that drive route
    handlers.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        yield session


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An httpx client bound to the FastAPI app via ASGITransport."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def load_script_module(name: str, script: str | None = None) -> ModuleType:
    """Load ``scripts/<script or name>.py`` as a module registered under `name`.

    ``scripts/`` is not a package, so tests import those files by path. The
    module is registered in ``sys.modules`` *before* exec so the script's
    ``@dataclass`` field resolution can find its own module during class
    creation (required on Python 3.14), and so tests can monkeypatch it via
    ``sys.modules[name]``. Pass a distinct `name` (e.g. per test file) to get
    an isolated copy of the same script; `script` defaults to `name`.
    """
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f"{script or name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def session_cookie(user_id: int) -> str:
    """Return a signed Starlette session cookie value for `user_id`.

    Matches the cookie the auth layer issues on sign-in. The payload carries
    `next_after_login` (the app only reads `user_id`, but keeping the full shape
    mirrors production).
    """
    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return signer.sign(payload).decode()


def signed_in_cookies(user_id: int) -> dict[str, str]:
    """Return a cookies dict that authenticates an httpx request as `user_id`."""
    return {"hhh_session": session_cookie(user_id)}
