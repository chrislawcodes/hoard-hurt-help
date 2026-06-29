"""Tests for the model-verification store + engine logic (slice 2a).

Covers sanitize_error (bound + scrub), record_results (outcome→status, FR-013
timeout escalation, upsert), and model_status_for (aggregate precedence).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db import make_engine
from app.engine.model_verification import (
    model_status_for,
    record_results,
    sanitize_error,
)
from app.models import Base
from app.models.connection import ConnectionProvider
from app.models.model_verification import ModelVerification, ModelVerificationStatus
from tests.factories import make_connection, make_user


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = make_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


# --- sanitize_error -----------------------------------------------------------


def test_sanitize_strips_tokens_and_paths() -> None:
    out = sanitize_error("failed with key sk_abc123def456 at /Users/me/.agentludum/x")
    assert out is not None
    assert "sk_abc123def456" not in out
    assert "/Users/me" not in out
    assert "[redacted]" in out


def test_sanitize_truncates_to_300() -> None:
    out = sanitize_error("e" * 500)
    assert out is not None and len(out) <= 300


def test_sanitize_none_and_empty() -> None:
    assert sanitize_error(None) is None
    assert sanitize_error("   ") is None


# --- record_results -----------------------------------------------------------


async def _status_of(db: AsyncSession, conn_id: int, model: str) -> ModelVerification:
    return (
        await db.execute(
            select(ModelVerification).where(
                ModelVerification.connection_id == conn_id,
                ModelVerification.model == model,
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_record_verified_and_failed(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 0)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    await db_session.flush()
    await record_results(
        db_session,
        conn,
        [
            {"provider": "claude", "model": "claude-opus-4-8", "outcome": "verified"},
            {
                "provider": "claude",
                "model": "claude-sonnet-4-6",
                "outcome": "failed",
                "error_text": "model not available on your plan",
            },
        ],
    )
    ok = await _status_of(db_session, conn.id, "claude-opus-4-8")
    bad = await _status_of(db_session, conn.id, "claude-sonnet-4-6")
    assert ok.status is ModelVerificationStatus.VERIFIED and ok.error_text is None
    assert bad.status is ModelVerificationStatus.FAILED
    assert bad.error_text == "model not available on your plan"


@pytest.mark.asyncio
async def test_timeout_escalates_to_failed_at_threshold(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 0)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    await db_session.flush()
    payload = [{"provider": "claude", "model": "claude-opus-4-8", "outcome": "timeout"}]
    await record_results(db_session, conn, payload)
    assert (await _status_of(db_session, conn.id, "claude-opus-4-8")).status is ModelVerificationStatus.TIMEOUT
    await record_results(db_session, conn, payload)  # 2nd → still timeout
    row = await _status_of(db_session, conn.id, "claude-opus-4-8")
    assert row.status is ModelVerificationStatus.TIMEOUT and row.consecutive_timeouts == 2
    await record_results(db_session, conn, payload)  # 3rd → escalates to failed
    row = await _status_of(db_session, conn.id, "claude-opus-4-8")
    assert row.status is ModelVerificationStatus.FAILED and row.consecutive_timeouts == 3


@pytest.mark.asyncio
async def test_verified_resets_timeout_streak(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 0)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    await db_session.flush()
    m = "claude-opus-4-8"
    await record_results(db_session, conn, [{"provider": "claude", "model": m, "outcome": "timeout"}])
    await record_results(db_session, conn, [{"provider": "claude", "model": m, "outcome": "verified"}])
    row = await _status_of(db_session, conn.id, m)
    assert row.status is ModelVerificationStatus.VERIFIED and row.consecutive_timeouts == 0


# --- model_status_for ---------------------------------------------------------


@pytest.mark.asyncio
async def test_status_for_verified_wins_over_failed(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 0)
    c1, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    c2, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE, key="sk_conn_two")
    now = datetime.now(timezone.utc)
    c1.last_polled_at = now  # live connectors (model_status_for counts live only)
    c2.last_polled_at = now
    await db_session.flush()
    m = "claude-opus-4-8"
    await record_results(db_session, c1, [{"provider": "claude", "model": m, "outcome": "failed"}])
    await record_results(db_session, c2, [{"provider": "claude", "model": m, "outcome": "verified"}])
    # One machine can't run it, another can → still runnable (no warning).
    assert await model_status_for(db_session, user.id, "claude", m) is ModelVerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_status_for_failed_everywhere(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 0)
    conn, _ = await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    conn.last_polled_at = datetime.now(timezone.utc)  # live connector
    await db_session.flush()
    m = "claude-opus-4-8"
    await record_results(db_session, conn, [{"provider": "claude", "model": m, "outcome": "failed"}])
    assert await model_status_for(db_session, user.id, "claude", m) is ModelVerificationStatus.FAILED


@pytest.mark.asyncio
async def test_status_for_unknown_when_never_checked(db_session: AsyncSession) -> None:
    user = await make_user(db_session, 0)
    await make_connection(db_session, user, provider=ConnectionProvider.CLAUDE)
    await db_session.flush()
    assert await model_status_for(db_session, user.id, "claude", "claude-opus-4-8") is ModelVerificationStatus.UNKNOWN
