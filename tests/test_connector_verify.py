"""Connector model-verification unit tests (slice 2c): the pure cadence gate,
the outcome classifier, and the per-provider test-call argv builder.

The poll-loop wiring itself is integration-light; these cover the logic that must
be correct (and that CI can guard without a live server)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "agentludum_connector.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("agentludum_connector_verify", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# --- _should_verify (cadence) -------------------------------------------------


def test_should_verify_false_before_interval(runner) -> None:
    now = 1000.0
    assert runner._should_verify(now, now - 30) is False  # only 30s since last


def test_should_verify_true_at_and_after_interval(runner) -> None:
    now = 1000.0
    assert runner._should_verify(now, now - runner._VERIFY_INTERVAL) is True
    assert runner._should_verify(now, now - 999) is True
    assert runner._should_verify(now, 0.0) is True  # first tick (last=0)


# --- _classify_verify (FR-005 success predicate + FR-009a mapping) ------------


def test_classify_verified_on_clean_exit_with_output(runner) -> None:
    assert runner._classify_verify(0, "ok", "", False) == "verified"


def test_classify_timeout_when_timed_out(runner) -> None:
    assert runner._classify_verify(0, "ok", "", True) == "timeout"


def test_classify_failed_on_model_unavailable_stderr(runner) -> None:
    for stderr in (
        "Error: model not found",
        "404 model does not exist",
        "unauthorized: no access to this model",
        "invalid model id",
    ):
        assert runner._classify_verify(1, "", stderr, False) == "failed", stderr


def test_classify_failed_on_logged_out_stdout(runner) -> None:
    # A signed-out CLI can exit 0 and print a login nudge to stdout — not a pass.
    assert runner._classify_verify(0, "Please run 'claude login' to continue", "", False) == "failed"
    assert runner._classify_verify(0, "ok", "you are not authenticated", False) == "failed"


def test_classify_timeout_on_generic_or_empty_failure(runner) -> None:
    # Unclassifiable failures default to retryable, never sticky-failed.
    assert runner._classify_verify(1, "", "some transient network blip", False) == "timeout"
    assert runner._classify_verify(1, "", "", False) == "timeout"
    # Clean exit but no output isn't a pass (FR-005 needs non-empty output).
    assert runner._classify_verify(0, "   ", "", False) == "timeout"


# --- _verify_argv -------------------------------------------------------------


def test_verify_argv_per_provider(runner) -> None:
    claude = runner._verify_argv("claude", "claude-opus-4-8")
    assert claude is not None and claude[0][0] == "claude" and "claude-opus-4-8" in claude[0]
    openai = runner._verify_argv("openai", "gpt-5.4-mini")
    assert openai is not None and openai[0][:2] == ["codex", "exec"] and "gpt-5.4-mini" in openai[0]
    gemini = runner._verify_argv("gemini", "gemini-3-flash-preview")
    assert gemini is not None and gemini[0][0] == "gemini"


def test_verify_argv_none_for_modelless_providers(runner) -> None:
    assert runner._verify_argv("hermes", "whatever") is None
    assert runner._verify_argv("openclaw", "whatever") is None
