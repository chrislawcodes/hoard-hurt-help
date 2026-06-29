"""Server-side model-verification logic: store, aggregate, sanitize.

Mirrors the provider-readiness shape one level down (per (connection, provider,
model)). The connector reports outcomes here; ``record_results`` persists them and
applies the FR-013 timeout-escalation; ``model_status_for`` aggregates across a
user's connections for the UI badge and the join warning; ``sanitize_error``
bounds and scrubs CLI stderr before it is ever stored or shown.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aware_datetime import ensure_aware
from app.config import PROVIDER_MODELS
from app.engine.connection_health_badge import (
    LOOP_RUNNING_WINDOW_SECONDS,
    within_window,
)
from app.engine.model_provider_match import default_model_for_provider, provider_for_model
from app.models.agent import Agent, AgentKind
from app.models.connection import Connection, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.model_verification import ModelVerification, ModelVerificationStatus

# After this many consecutive timeouts a model is stored as FAILED, so a
# chronically-timing-out model never sits in a silent retry loop (FR-013).
TIMEOUT_ESCALATION_THRESHOLD = 3

# Re-verify a model at most this often; a fresher cached result is skipped so the
# connector doesn't re-test every tick (FR-016). A new/changed model has no row,
# so it is verified on the next tick.
REFRESH_INTERVAL = timedelta(hours=6)


async def compute_worklist(
    db: AsyncSession, connection: Connection, *, now: datetime | None = None
) -> list[dict[str, str]]:
    """The (provider, model) pairs this connection should verify.

    For each of the connection's ENABLED providers with a non-empty allowlist:
    the provider's default model (the model most seats actually run, since
    preferred_model is NULL for nearly all agents) PLUS the distinct non-NULL
    `Agent.preferred_model` values for the user that belong to that provider.
    A pair already verified within REFRESH_INTERVAL is skipped (FR-016).
    """
    now = now or datetime.now(timezone.utc)
    enabled = (
        (
            await db.execute(
                select(ConnectionProviderRow.provider).where(
                    ConnectionProviderRow.connection_id == connection.id,
                    ConnectionProviderRow.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    enabled_providers = {
        p.value for p in enabled if PROVIDER_MODELS.get(p.value)
    }  # non-empty allowlist only
    if not enabled_providers:
        return []

    # Desired (provider, model) set: provider defaults + matching preferreds.
    desired: set[tuple[str, str]] = set()
    for provider in enabled_providers:
        default = default_model_for_provider(provider)
        if default:
            desired.add((provider, default))
    preferreds = (
        (
            await db.execute(
                select(Agent.preferred_model).where(
                    Agent.user_id == connection.user_id,
                    Agent.kind == AgentKind.AI,
                    Agent.archived_at.is_(None),
                    Agent.preferred_model.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for model in {m for m in preferreds if m}:
        model_provider = provider_for_model(model)
        if model_provider and model_provider in enabled_providers:
            desired.add((model_provider, model))

    # Drop pairs already checked within the refresh window.
    fresh_cutoff = now - REFRESH_INTERVAL
    rows = (
        (
            await db.execute(
                select(
                    ModelVerification.provider,
                    ModelVerification.model,
                    ModelVerification.checked_at,
                ).where(ModelVerification.connection_id == connection.id)
            )
        )
        .all()
    )
    fresh = {
        (provider, model)
        for provider, model, checked_at in rows
        if checked_at is not None and ensure_aware(checked_at) > fresh_cutoff
    }
    return [
        {"provider": provider, "model": model}
        for provider, model in sorted(desired - fresh)
    ]

_MAX_ERROR_LEN = 300
# Token-shaped secrets and home/temp absolute paths get redacted before storage.
_REDACTIONS = (
    re.compile(r"\b(sk_[A-Za-z0-9_-]{6,}|ghp_[A-Za-z0-9]{6,}|Bearer\s+\S+)"),
    re.compile(r"/(?:Users|home|private|var|tmp)/\S+"),
)


def sanitize_error(text: str | None) -> str | None:
    """Bound to 300 chars and strip paths/token-shaped secrets (FR-015)."""
    if not text:
        return None
    cleaned = text.strip()
    for pattern in _REDACTIONS:
        cleaned = pattern.sub("[redacted]", cleaned)
    if len(cleaned) > _MAX_ERROR_LEN:
        cleaned = cleaned[: _MAX_ERROR_LEN - 1].rstrip() + "…"
    return cleaned or None


def _status_for_outcome(
    outcome: str, prior_timeouts: int
) -> tuple[ModelVerificationStatus, int]:
    """Map a reported outcome to a stored status + new consecutive-timeout count.

    verified/failed reset the timeout streak; a timeout increments it and escalates
    to FAILED at the threshold (FR-013). An unrecognized outcome is treated as a
    retryable timeout (the conservative default — never a sticky failure).
    """
    if outcome == "verified":
        return ModelVerificationStatus.VERIFIED, 0
    if outcome == "failed":
        return ModelVerificationStatus.FAILED, 0
    # outcome == "timeout" or anything unrecognized → retryable, with escalation.
    streak = prior_timeouts + 1
    if streak >= TIMEOUT_ESCALATION_THRESHOLD:
        return ModelVerificationStatus.FAILED, streak
    return ModelVerificationStatus.TIMEOUT, streak


async def record_results(
    db: AsyncSession,
    connection: Connection,
    results: list[dict[str, str | None]],
) -> None:
    """Upsert verification outcomes for a connection.

    Each result is ``{"provider", "model", "outcome", "error_text"}``. Existing
    rows are updated in place; new ones are created. Caller commits.
    """
    now = datetime.now(timezone.utc)
    for result in results:
        provider = str(result.get("provider") or "").lower()
        model = str(result.get("model") or "")
        outcome = str(result.get("outcome") or "")
        if not provider or not model:
            continue
        row = (
            await db.execute(
                select(ModelVerification).where(
                    ModelVerification.connection_id == connection.id,
                    ModelVerification.provider == provider,
                    ModelVerification.model == model,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            # Set consecutive_timeouts explicitly: the column default applies only
            # at INSERT, so a freshly-created (unflushed) row reads None otherwise.
            row = ModelVerification(
                connection_id=connection.id,
                provider=provider,
                model=model,
                consecutive_timeouts=0,
            )
            db.add(row)
        status, streak = _status_for_outcome(outcome, row.consecutive_timeouts)
        row.status = status
        row.consecutive_timeouts = streak
        row.error_text = (
            sanitize_error(result.get("error_text"))
            if status is not ModelVerificationStatus.VERIFIED
            else None
        )
        row.checked_at = now


# Aggregation precedence: a single verified row makes a model runnable; a
# not-yet-known (checking) or retryable (timeout) state suppresses a failure
# warning; only an all-failed picture surfaces as FAILED.
_PRECEDENCE = [
    ModelVerificationStatus.VERIFIED,
    ModelVerificationStatus.CHECKING,
    ModelVerificationStatus.TIMEOUT,
    ModelVerificationStatus.FAILED,
    ModelVerificationStatus.UNKNOWN,
]


async def model_status_for(
    db: AsyncSession, user_id: int, provider: str, model: str
) -> ModelVerificationStatus:
    """Aggregate a model's status across a user's active connections.

    Returns the highest-precedence status seen (verified wins), or UNKNOWN when no
    connection has reported. The join warning (slice 4b) fires only on FAILED —
    i.e. at least one connection reports failure and none reports verified/checking.
    """
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(ModelVerification.status, Connection.last_polled_at)
            .join(Connection, Connection.id == ModelVerification.connection_id)
            .where(
                Connection.user_id == user_id,
                Connection.status == ConnectionStatus.ACTIVE,
                Connection.deleted_at.is_(None),
                ModelVerification.provider == provider.lower(),
                ModelVerification.model == model,
            )
        )
    ).all()
    # Only LIVE machine connections count (a connector actively polling within the
    # loop-running window). A turned-off connector is not auto-demoted from ACTIVE,
    # so without this a stale row would keep falsely failing the model; and an
    # MCP-only connection never polls → never live → correctly excluded (FR-014).
    live = {
        status
        for status, last_polled in rows
        if last_polled is not None
        and within_window(ensure_aware(last_polled), now, LOOP_RUNNING_WINDOW_SECONDS)
    }
    for status in _PRECEDENCE:
        if status in live:
            return status
    return ModelVerificationStatus.UNKNOWN
