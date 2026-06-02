"""The unified agent runner dispatches to the right provider/model.

This is the guarantee that fixes the "Gemini bot silently ran on Claude" class of
bug: the runner picks the CLI from the bot's configured provider (sent by the
server as `preferred_provider`), not from which file you launched.

The script lives in scripts/ and is downloaded/run standalone, so we import it by
path and exercise its pure resolution logic (no real CLI calls).
"""

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "agentludum_agent.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("agentludum_agent_unified", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so the @dataclass decorator can resolve the module
    # (Python 3.14 looks it up in sys.modules during class creation).
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _args(provider=None, model=None):
    return argparse.Namespace(provider=provider, model=model)


def _turn(preferred_provider=None, preferred_model=None):
    return {"preferred_provider": preferred_provider, "preferred_model": preferred_model}


def test_each_provider_maps_to_the_right_cli(runner):
    assert runner._ADAPTERS["claude"].cli == "claude"
    assert runner._ADAPTERS["openai"].cli == "codex"
    assert runner._ADAPTERS["gemini"].cli == "gemini"


def test_bot_config_drives_provider_and_model(runner):
    # No flags → the bot's configured provider + model win. This is the fix:
    # a Gemini bot resolves to the gemini CLI, never Claude.
    assert runner._resolve(_turn("gemini", "gemini-3-flash-preview"), _args()) == (
        "gemini",
        "gemini-3-flash-preview",
    )
    prov, _ = runner._resolve(_turn("gemini", None), _args())
    assert runner._ADAPTERS[prov].cli == "gemini"
    assert runner._resolve(_turn("openai", "gpt-5.4-mini"), _args()) == ("openai", "gpt-5.4-mini")


def test_unset_provider_defaults_to_claude(runner):
    # A brand-new bot with no provider configured falls back to Claude.
    assert runner._resolve(_turn(None, None), _args()) == ("claude", "claude-haiku-4-5")


def test_non_cli_provider_falls_back_to_claude(runner):
    # Hermes/OpenClaw play over MCP — no CLI runner — so this script falls back.
    assert runner._resolve(_turn("hermes", None), _args()) == ("claude", "claude-haiku-4-5")


def test_provider_flag_overrides_and_drops_other_providers_model(runner):
    # --provider override uses a different CLI, so the bot's configured model (for
    # its real provider) must NOT leak into the overridden CLI — use the default.
    assert runner._resolve(
        _turn("gemini", "gemini-3-flash-preview"), _args(provider="claude")
    ) == ("claude", "claude-haiku-4-5")


def test_model_flag_always_wins(runner):
    assert runner._resolve(
        _turn("claude", "claude-haiku-4-5"), _args(model="claude-opus-4-8")
    ) == ("claude", "claude-opus-4-8")


def test_first_turn_folds_framing_for_codex_gemini_not_claude(runner):
    # Claude carries framing in --system-prompt (separate); codex/gemini fold it
    # into the first message. Verify the body/framing split is wired per adapter.
    sess = runner._GameSession(provider="gemini", model="m")
    captured = {}

    def fake_call(self, session_id, model, prompt, *, resume):
        captured["prompt"] = prompt
        return '{"action":"HOARD","target_id":null,"thinking":"x"}'

    runner._GeminiAdapter._call = fake_call  # type: ignore[method-assign]
    runner._ADAPTERS["gemini"].first(
        body="BODY", framing="FRAMING", model="m", session=sess
    )
    assert "FRAMING" in captured["prompt"] and "BODY" in captured["prompt"]
    assert sess.token is not None  # gemini assigns its own session UUID
