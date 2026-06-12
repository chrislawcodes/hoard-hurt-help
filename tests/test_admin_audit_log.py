"""Tests for AdminAuditLog model and migration 0029."""

from __future__ import annotations

from app.models import AdminAction, AdminAuditLog


def test_admin_audit_log_importable() -> None:
    """AdminAuditLog and AdminAction are importable from app.models."""
    assert AdminAuditLog.__tablename__ == "admin_audit_log"
    assert AdminAction.disable.value == "disable"
    assert AdminAction.enable.value == "enable"
    assert AdminAction.promote.value == "promote"
    assert AdminAction.demote.value == "demote"
    assert AdminAction.handle_reset.value == "handle_reset"


def test_user_has_disabled_at() -> None:
    """User model has disabled_at column."""
    from app.models.user import User

    col_names = {c.name for c in User.__table__.columns}
    assert "disabled_at" in col_names


def test_admin_audit_log_indexes() -> None:
    """admin_audit_log has indexes on actor_user_id and target_user_id."""
    table = AdminAuditLog.__table__
    index_cols = {col.name for idx in table.indexes for col in idx.columns}
    assert "actor_user_id" in index_cols
    assert "target_user_id" in index_cols


def test_admin_audit_log_fk_restrict() -> None:
    """FKs on admin_audit_log use RESTRICT ondelete."""
    table = AdminAuditLog.__table__
    fk_map = {fk.parent.name: fk.ondelete for fk in table.foreign_keys}
    assert fk_map["actor_user_id"] == "RESTRICT"
    assert fk_map["target_user_id"] == "RESTRICT"
