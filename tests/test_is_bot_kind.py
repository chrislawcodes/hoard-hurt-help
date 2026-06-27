"""C3 dedup: the single value-level bot-kind predicate.

Pins that `is_bot_kind` accepts both the enum member and its raw string value
(a superset of the old DB-level `kind == AgentKind.BOT` check) and rejects
non-bot kinds, so the `turn_drivers` (DB) and `arena` (inline) call sites that
now delegate here keep identical behavior.
"""
from __future__ import annotations

from app.engine.user_match_start import is_bot_kind
from app.models.agent import AgentKind


def test_is_bot_kind_accepts_member_and_value() -> None:
    assert is_bot_kind(AgentKind.BOT) is True
    assert is_bot_kind(AgentKind.BOT.value) is True  # raw "bot"


def test_is_bot_kind_rejects_non_bot_kinds() -> None:
    assert is_bot_kind(AgentKind.AI) is False
    assert is_bot_kind(AgentKind.AI.value) is False
    assert is_bot_kind(AgentKind.HUMAN) is False
    assert is_bot_kind(None) is False
    assert is_bot_kind("not-a-kind") is False
