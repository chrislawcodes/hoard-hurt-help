"""Regression guard: ONE source of truth for the move-text caps.

The public `message` and private `thinking` caps used to be hand-copied as bare `200`
literals across the server schemas and the standalone connector, and drifted apart
twice — when they drift, the server 422-rejects an over-cap move and the agent's move is
silently dropped.

This module pins every enforcing consumer to the single source of truth in
`app.agent_prompt` (`MESSAGE_MAX_LENGTH`, `THINKING_MAX_LENGTH`):

- the four Pydantic schema sites (`SubmitRequest`/`MessageRequest` × message/thinking),
- the connector's LIVE clip behaviour (`_normalize_move`, `_move_request`),
- the connector's STANDALONE fallback constants (the values an operator actually runs
  with when `app/` is absent), including a load that exercises the real
  `except ImportError` branch,
- the model-facing protocol guidance text.

Any of these diverging from the source of truth fails this test.
"""

from __future__ import annotations

import builtins
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import BaseModel

from app.agent_prompt import (
    MESSAGE_MAX_LENGTH,
    RESPONSE_PROTOCOL,
    THINKING_MAX_LENGTH,
)
from app.schemas.agent import MessageRequest, SubmitRequest

_CONNECTOR = Path(__file__).resolve().parents[1] / "scripts" / "agentludum_connector.py"


def _load_connector(module_name: str) -> ModuleType:
    """Load the standalone connector script as a module (same pattern as
    tests/test_connector_fallback.py)."""
    spec = importlib.util.spec_from_file_location(module_name, _CONNECTOR)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so the connector's @dataclass field resolution can find
    # its own module via sys.modules (required on Python 3.14).
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def connector() -> ModuleType:
    return _load_connector("agentludum_connector_limits")


def _field_max_length(model: type[BaseModel], name: str) -> int:
    """Read a Pydantic v2 field's max_length from its constraint metadata.

    Scans metadata for the constraint carrying a `max_length` attribute rather than
    hard-indexing metadata[0], so a change in metadata ordering does not silently
    break the check (it would fail loudly instead).
    """
    field = model.model_fields[name]
    for constraint in field.metadata:
        value = getattr(constraint, "max_length", None)
        if value is not None:
            return int(value)
    raise AssertionError(f"{model.__name__}.{name} has no max_length constraint")


# --- US4: values unchanged ------------------------------------------------------


def test_values_unchanged() -> None:
    assert MESSAGE_MAX_LENGTH == 200
    assert THINKING_MAX_LENGTH == 200


# --- US1: all four server enforcement sites derive from the source --------------


def test_all_four_schema_sites_derive_from_source() -> None:
    cases: list[tuple[type[BaseModel], str, int]] = [
        (SubmitRequest, "message", MESSAGE_MAX_LENGTH),
        (SubmitRequest, "thinking", THINKING_MAX_LENGTH),
        (MessageRequest, "message", MESSAGE_MAX_LENGTH),
        (MessageRequest, "thinking", THINKING_MAX_LENGTH),
    ]
    for model, name, expected in cases:
        assert _field_max_length(model, name) == expected


# --- US3: connector LIVE clip behaviour tracks the source -----------------------


def test_normalize_move_clips_to_source(connector: ModuleType) -> None:
    over_msg = "x" * (MESSAGE_MAX_LENGTH + 50)
    over_think = "y" * (THINKING_MAX_LENGTH + 50)
    talk = connector._normalize_move(
        {"message": over_msg, "thinking": over_think}, "talk"
    )
    assert len(talk["message"]) == MESSAGE_MAX_LENGTH
    assert len(talk["thinking"]) == THINKING_MAX_LENGTH
    act = connector._normalize_move(
        {"action": "HOARD", "thinking": over_think}, "act"
    )
    assert len(act["thinking"]) == THINKING_MAX_LENGTH


def _turn(phase: str) -> dict[str, Any]:
    return {
        "agent_turn_token": "att",
        "current": {"turn_token": "tt", "phase": phase},
    }


def test_move_request_body_clips_to_source(connector: ModuleType) -> None:
    over_msg = "x" * (MESSAGE_MAX_LENGTH + 50)
    over_think = "y" * (THINKING_MAX_LENGTH + 50)

    _, _, talk_body = connector._move_request(
        "http://h", "M1", _turn("talk"),
        {"message": over_msg, "thinking": over_think},
    )
    assert len(talk_body["message"]) == MESSAGE_MAX_LENGTH
    assert len(talk_body["thinking"]) == THINKING_MAX_LENGTH

    _, _, act_body = connector._move_request(
        "http://h", "M1", _turn("act"),
        {"action": "HOARD", "thinking": over_think},
    )
    assert len(act_body["thinking"]) == THINKING_MAX_LENGTH


# --- US3: connector standalone fallback constants are pinned to the source ------


def test_connector_fallback_matches_server(connector: ModuleType) -> None:
    # Reads the connector's STANDALONE fallback constants — the values an operator
    # runs with when app/ is absent — NOT the import-resolved values.
    assert connector._FALLBACK_MESSAGE_MAX_LENGTH == MESSAGE_MAX_LENGTH
    assert connector._FALLBACK_THINKING_MAX_LENGTH == THINKING_MAX_LENGTH


def test_connector_loads_with_app_unimportable() -> None:
    """Re-load the connector with `app` blocked from import, simulating the real
    operator machine where app/ is absent. This actually runs the
    `except ImportError` branch and proves the fallback binding works AND still
    matches the server source. Without this, the checkout's clean `app` import means
    the except-branch is never exercised by tests."""
    real_import = builtins.__import__

    def _blocked(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "app" or name.startswith("app."):
            raise ImportError("simulated standalone: app/ absent")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = _blocked
    try:
        mod = _load_connector("agentludum_connector_standalone")
    finally:
        builtins.__import__ = real_import

    assert mod._CANONICAL_PROTOCOL is None  # confirms the except-branch actually ran
    assert mod._MESSAGE_MAX_LENGTH == MESSAGE_MAX_LENGTH
    assert mod._THINKING_MAX_LENGTH == THINKING_MAX_LENGTH


# --- guidance text now derives from the source ----------------------------------


def test_protocol_text_renders_the_source_value() -> None:
    assert f"max {MESSAGE_MAX_LENGTH} chars" in RESPONSE_PROTOCOL
    assert f"max {THINKING_MAX_LENGTH} chars" in RESPONSE_PROTOCOL
