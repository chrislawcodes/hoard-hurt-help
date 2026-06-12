"""Admin user-action helpers: disable, enable, promote, demote, handle_reset.

Each helper:
  1. Loads the target user with an optional row-lock (Postgres only).
  2. Applies a no-op guard - returns without writing an audit row if the
     target is already in the requested state.
  3. Refuses floor-admin targets for demote and disable (case-insensitive
     match against settings.platform_admin_emails_set).
  4. Mutates the user and writes exactly one AdminAuditLog row in the
     same transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.admin_audit_log import AdminAction, AdminAuditLog
from app.models.user import User, UserRole


def _is_floor_admin(user: User) -> bool:
    return user.email.lower() in settings.platform_admin_emails_set


async def _load_target(db: AsyncSession, target_id: int) -> User:
    """Load user by id with an optional write-lock (skipped on SQLite)."""
    use_lock = db.sync_session.get_bind().dialect.name != "sqlite"
    stmt = select(User).where(User.id == target_id)
    if use_lock:
        stmt = stmt.with_for_update()
    target = (await db.execute(stmt)).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return target


def _write_audit(
    db: AsyncSession,
    *,
    actor: User,
    target: User,
    action: AdminAction,
    reason: str | None = None,
) -> None:
    db.add(
        AdminAuditLog(
            actor_user_id=actor.id,
            target_user_id=target.id,
            action=action,
            reason=reason,
        )
    )


async def disable_user(
    db: AsyncSession,
    *,
    actor: User,
    target_id: int,
    reason: str | None = None,
) -> None:
    target = await _load_target(db, target_id)
    if target.disabled_at is not None:
        return
    if _is_floor_admin(target):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot disable a platform-admin-floor user.",
        )
    target.disabled_at = datetime.now(timezone.utc)
    _write_audit(db, actor=actor, target=target, action=AdminAction.disable, reason=reason)


async def enable_user(
    db: AsyncSession,
    *,
    actor: User,
    target_id: int,
    reason: str | None = None,
) -> None:
    target = await _load_target(db, target_id)
    if target.disabled_at is None:
        return
    target.disabled_at = None
    _write_audit(db, actor=actor, target=target, action=AdminAction.enable, reason=reason)


async def promote_user(
    db: AsyncSession,
    *,
    actor: User,
    target_id: int,
    reason: str | None = None,
) -> None:
    target = await _load_target(db, target_id)
    if target.role == UserRole.ADMIN:
        return
    target.role = UserRole.ADMIN
    _write_audit(db, actor=actor, target=target, action=AdminAction.promote, reason=reason)


async def demote_user(
    db: AsyncSession,
    *,
    actor: User,
    target_id: int,
    reason: str | None = None,
) -> None:
    target = await _load_target(db, target_id)
    if target.role == UserRole.USER:
        return
    if _is_floor_admin(target):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot demote a platform-admin-floor user.",
        )
    target.role = UserRole.USER
    _write_audit(db, actor=actor, target=target, action=AdminAction.demote, reason=reason)


async def reset_handle(
    db: AsyncSession,
    *,
    actor: User,
    target_id: int,
    reason: str | None = None,
) -> None:
    target = await _load_target(db, target_id)
    if target.handle is None:
        return
    target.handle = None
    target.handle_key = None
    target.handle_changed_at = None
    _write_audit(db, actor=actor, target=target, action=AdminAction.handle_reset, reason=reason)
