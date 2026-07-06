"""Connector model-verification unit tests (slice 2c): the pure cadence gate,
the outcome classifier, and the per-provider test-call argv builder.

The poll-loop wiring itself is integration-light; these cover the logic that must
be correct (and that CI can guard without a live server)."""

from __future__ import annotations

import subprocess

import pytest

from tests.conftest import load_script_module


@pytest.fixture(scope="module")
def runner():
    return load_script_module("agentludum_connector_verify", "agentludum_connector")


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


# --- _classify_play_failure (slice 3: fail-loud at play time) ------------------


def test_play_failure_timeout(runner) -> None:
    outcome, _ = runner._classify_play_failure(subprocess.TimeoutExpired("claude", 180))
    assert outcome == "timeout"


def test_play_failure_failed_on_model_unavailable(runner) -> None:
    outcome, reason = runner._classify_play_failure(RuntimeError("model not found (404)"))
    assert outcome == "failed"
    assert "not found" in reason


def test_play_failure_timeout_on_generic_error(runner) -> None:
    # An unclassifiable failure is retryable, never sticky-failed.
    outcome, _ = runner._classify_play_failure(RuntimeError("subprocess exploded"))
    assert outcome == "timeout"


# --- _provider_from_model (authoritative allowlist vs. prefix heuristic) -------


def test_provider_from_model_agrees_with_authoritative_allowlist(runner) -> None:
    """In a source checkout the connector defers to app.config.provider_for_model
    instead of its own prefix heuristic (the two can disagree — #569)."""
    from app.config import PROVIDER_MODELS, provider_for_model

    assert runner._authoritative_provider_for_model is provider_for_model

    # Every real model resolves to exactly what the allowlist says.
    for provider, models in PROVIDER_MODELS.items():
        for model in models:
            assert runner._provider_from_model(model) == provider_for_model(model) == provider


def test_provider_from_model_returns_none_for_prefixed_but_unlisted_model(runner) -> None:
    """A model with a known prefix that is NOT in the allowlist is the divergence
    case: the old prefix heuristic would have guessed a provider, but the
    authoritative mapping (and now the connector) returns None so the caller
    resolves the provider from the stored agent instead of mis-attributing it."""
    unlisted = "claude-does-not-exist-99"
    from app.config import provider_for_model

    assert provider_for_model(unlisted) is None
    # The connector agrees with the authoritative mapping, not the prefix guess.
    assert runner._provider_from_model(unlisted) is None


def test_provider_from_model_delegates_to_config(runner, monkeypatch) -> None:
    """The connector consults the authoritative function, not a hardcoded copy:
    monkeypatching it changes the connector's answer."""
    calls: list[str] = []

    def fake(model: str) -> str | None:
        calls.append(model)
        return "sentinel-provider"

    monkeypatch.setattr(runner, "_authoritative_provider_for_model", fake)
    assert runner._provider_from_model("gpt-5.4-mini") == "sentinel-provider"
    assert calls == ["gpt-5.4-mini"]


# --- _http (pooled connection client) -----------------------------------------


def test_http_returns_pooled_singleton(runner) -> None:
    """The connector reuses ONE httpx.Client for the whole run (keep-alive
    connection pooling) instead of opening a fresh connection per request."""
    import httpx

    runner._http_client = None  # start from a clean slate
    try:
        c1 = runner._http()
        c2 = runner._http()
        assert c1 is c2
        assert isinstance(c1, httpx.Client)
    finally:
        if runner._http_client is not None:
            runner._http_client.close()
        runner._http_client = None
