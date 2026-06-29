"""Tests for the model-verification channels (slice 2b): compute_worklist + the
down/up endpoints.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.engine.model_verification import compute_worklist, record_results
from app.models.connection import ConnectionProvider
from tests.factories import make_agent, make_connection, make_user


def _pairs(worklist: list[dict[str, str]]) -> set[tuple[str, str]]:
    return {(w["provider"], w["model"]) for w in worklist}


# --- compute_worklist ---------------------------------------------------------


@pytest.mark.asyncio
async def test_worklist_includes_provider_default_for_null_preferred(
    db: AsyncSession,
) -> None:
    # The HIGH from plan review: with no preferred model, the worklist must still
    # include the provider default — the model the seat actually runs.
    user = await make_user(db, 0)
    conn, _ = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
    await db.flush()
    pairs = _pairs(await compute_worklist(db, conn))
    assert ("claude", "claude-haiku-4-5") in pairs


@pytest.mark.asyncio
async def test_worklist_includes_matching_preferred_and_excludes_other_provider(
    db: AsyncSession,
) -> None:
    user = await make_user(db, 0)
    conn, _ = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
    # A claude-preferred agent → included; a gpt-preferred agent on a claude-only
    # connection → excluded (provider not enabled here).
    a1, _ = await make_agent(db, user, name="opus-agent")
    a1.preferred_model = "claude-opus-4-8"
    a2, _ = await make_agent(db, user, name="gpt-agent")
    a2.preferred_model = "gpt-5.4-mini"
    await db.flush()
    pairs = _pairs(await compute_worklist(db, conn))
    assert ("claude", "claude-opus-4-8") in pairs
    assert ("claude", "gpt-5.4-mini") not in pairs
    assert not any(p == "openai" for p, _ in pairs)


@pytest.mark.asyncio
async def test_worklist_skips_fresh_and_reincludes_stale(db: AsyncSession) -> None:
    user = await make_user(db, 0)
    conn, _ = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
    await db.flush()
    await record_results(
        db, conn, [{"provider": "claude", "model": "claude-haiku-4-5", "outcome": "verified"}]
    )
    # Just verified → excluded from the worklist now.
    assert ("claude", "claude-haiku-4-5") not in _pairs(await compute_worklist(db, conn))
    # 7 hours later → past the 6h refresh window → back in the worklist.
    later = datetime.now(timezone.utc) + timedelta(hours=7)
    assert ("claude", "claude-haiku-4-5") in _pairs(
        await compute_worklist(db, conn, now=later)
    )


@pytest.mark.asyncio
async def test_worklist_empty_for_empty_allowlist_provider(db: AsyncSession) -> None:
    user = await make_user(db, 0)
    conn, _ = await make_connection(db, user, provider=ConnectionProvider.HERMES)
    await db.flush()
    assert await compute_worklist(db, conn) == []


# --- endpoints ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_worklist_endpoint_requires_connection_key(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    r = await client.get("/api/agent/model-worklist")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_verification_roundtrip(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    async with reset_db() as db:
        user = await make_user(db, 0)
        conn, key = await make_connection(db, user, provider=ConnectionProvider.CLAUDE)
        await db.commit()

    headers = {"X-Connection-Key": key}
    # Worklist starts with the claude default.
    r = await client.get("/api/agent/model-worklist", headers=headers)
    assert r.status_code == 200, r.text
    assert ("claude", "claude-haiku-4-5") in _pairs(r.json()["worklist"])

    # Report it verified.
    r = await client.post(
        "/api/agent/model-verification",
        headers=headers,
        json={"results": [{"provider": "claude", "model": "claude-haiku-4-5", "outcome": "verified"}]},
    )
    assert r.status_code == 204, r.text

    # Now it's fresh → gone from the worklist.
    r = await client.get("/api/agent/model-worklist", headers=headers)
    assert ("claude", "claude-haiku-4-5") not in _pairs(r.json()["worklist"])
