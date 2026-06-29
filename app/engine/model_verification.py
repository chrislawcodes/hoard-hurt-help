"""Server-side model-verification logic: store, aggregate, sanitize.

Mirrors the provider-readiness shape one level down (per (connection, provider,
model)). The connector reports outcomes here; ``record_results`` persists them and
applies the FR-013 timeout-escalation; ``model_status_for`` aggregates across a
user's connections for the UI badge and the join warning; ``sanitize_error``
bounds and scrubs CLI stderr before it is ever stored or shown.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connection import Connection, ConnectionStatus
from app.models.model_verification import ModelVerification, ModelVerificationStatus

# After this many consecutive timeouts a model is stored as FAILED, so a
# chronically-timing-out model never sits in a silent retry loop (FR-013).
TIMEOUT_ESCALATION_THRESHOLD = 3

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
    rows = (
        await db.execute(
            select(ModelVerification.status)
            .join(Connection, Connection.id == ModelVerification.connection_id)
            .where(
                Connection.user_id == user_id,
                Connection.status == ConnectionStatus.ACTIVE,
                Connection.deleted_at.is_(None),
                ModelVerification.provider == provider.lower(),
                ModelVerification.model == model,
            )
        )
    ).scalars().all()
    seen = set(rows)
    for status in _PRECEDENCE:
        if status in seen:
            return status
    return ModelVerificationStatus.UNKNOWN
