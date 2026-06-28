"""Tests for `model_for_provider` — the payload guard that stops a seat from
being handed a model that belongs to a *different* provider.

The bug this guards: a legacy agent version carrying a ``gpt-*`` model, seated as
Claude, made the turn payload send ``model=gpt-5.4-mini`` to the claude CLI,
which 404s and falls back to HOARD every turn. The guard drops a model only when
it provably belongs to another provider; otherwise it passes through. (The
model→provider mapping itself lives in ``app.config.provider_for_model`` and is
tested there.)
"""

from __future__ import annotations

from app.engine.model_provider_match import (
    default_model_for_provider,
    model_for_provider,
    resolve_seat_model,
)


def test_mismatched_known_model_is_dropped() -> None:
    # The core bug: a Claude seat carrying a gpt model must NOT forward it, and
    # vice versa. The connector then falls back to the provider's default model.
    assert model_for_provider("claude", "gpt-5.4-mini") is None
    assert model_for_provider("openai", "claude-haiku-4-5") is None
    assert model_for_provider("gemini", "gpt-5.4") is None


def test_matching_model_is_forwarded() -> None:
    assert model_for_provider("openai", "gpt-5.4-mini") == "gpt-5.4-mini"
    assert model_for_provider("claude", "claude-haiku-4-5") == "claude-haiku-4-5"
    assert model_for_provider("gemini", "gemini-3-flash-preview") == "gemini-3-flash-preview"


def test_unrecognized_model_passes_through() -> None:
    # A plausible same-provider model that isn't in the allowlist is trusted, not
    # second-guessed (e.g. a newer/older Claude model on a Claude seat).
    assert model_for_provider("claude", "claude-opus-4-1") == "claude-opus-4-1"
    assert model_for_provider("openai", "some-future-model") == "some-future-model"


def test_no_model_returns_none() -> None:
    assert model_for_provider("claude", None) is None
    assert model_for_provider("openai", "") is None


def test_no_provider_passes_model_through() -> None:
    # Seat not yet claimed (no chosen provider) → forward the model and let the
    # connector derive the provider from it.
    assert model_for_provider(None, "gpt-5.4-mini") == "gpt-5.4-mini"


# --- default_model_for_provider ------------------------------------------------


def test_default_model_is_first_allowlist_entry() -> None:
    # The per-provider default is the first PROVIDER_MODELS entry.
    assert default_model_for_provider("claude") == "claude-haiku-4-5"
    assert default_model_for_provider("openai") == "gpt-5.4-mini"
    assert default_model_for_provider("CLAUDE") == "claude-haiku-4-5"  # case-insensitive


def test_default_model_none_for_empty_or_unknown_provider() -> None:
    # MCP-only providers have empty allowlists → no server default.
    assert default_model_for_provider("hermes") is None
    assert default_model_for_provider("openclaw") is None
    assert default_model_for_provider("nonsense") is None
    assert default_model_for_provider(None) is None


# --- resolve_seat_model (the three-layer resolution) ---------------------------


def test_resolve_prefers_matching_preferred_model() -> None:
    # Layer 1: a preferred model that belongs to the provider is kept.
    assert resolve_seat_model("claude", "claude-opus-4-8") == "claude-opus-4-8"
    assert resolve_seat_model("openai", "gpt-5.5") == "gpt-5.5"


def test_resolve_falls_back_to_default_on_mismatch() -> None:
    # Layer 2: a preferred model from a different provider never reaches the CLI;
    # the provider's default is used instead.
    assert resolve_seat_model("claude", "gpt-5.4-mini") == "claude-haiku-4-5"
    assert resolve_seat_model("openai", "claude-haiku-4-5") == "gpt-5.4-mini"


def test_resolve_uses_default_when_unset() -> None:
    # Layer 2: no preferred model → provider default.
    assert resolve_seat_model("claude", None) == "claude-haiku-4-5"
    assert resolve_seat_model("openai", "") == "gpt-5.4-mini"


def test_resolve_none_for_empty_allowlist_provider() -> None:
    # Layer 3: an MCP-only provider has no default → None (connector decides).
    assert resolve_seat_model("hermes", None) is None
    assert resolve_seat_model("openclaw", "anything") is None
