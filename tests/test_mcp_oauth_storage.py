"""MCP OAuth token/client store selection (deploy-survivable sessions).

The OAuth proxy holds its registered-client + upstream-token records in a
key-value store. In production (Postgres) that store must be durable so sign-in
survives a redeploy; in dev/test (SQLite) in-memory is fine. These tests cover the
selection logic without needing a live Postgres (the Postgres store constructs
lazily — no connection at build time).
"""

from __future__ import annotations

import pytest

from app.config import settings
from mcp_server import server


def test_client_storage_is_memory_for_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    from key_value.aio.stores.memory import MemoryStore

    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///./test.db")
    store = server._build_client_storage()
    assert isinstance(store, MemoryStore)


def test_client_storage_is_durable_encrypted_for_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    monkeypatch.setattr(
        settings, "database_url", "postgresql+asyncpg://u:p@db.example.com:5432/app"
    )
    # FernetEncryptionWrapper needs a non-empty secret to derive its key.
    monkeypatch.setattr(settings, "mcp_jwt_signing_key", "a-stable-signing-secret-123456")
    store = server._build_client_storage()
    # Durable (Postgres-backed) AND encrypted at rest, not the in-memory store.
    assert isinstance(store, FernetEncryptionWrapper)


def test_auth_provider_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    # SQLite path → MemoryStore; must construct cleanly (this is what runs at import).
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///./test.db")
    provider = server._build_auth_provider()
    assert provider is not None
