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

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "agentludum_connector.py"


@pytest.fixture(scope="module")
def runner():
    # Load under the module's real name so the adapters resolve `_run` from the
    # same object the tests patch via sys.modules["agentludum_connector"].
    spec = importlib.util.spec_from_file_location("agentludum_connector", _SCRIPT)
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


def test_hermes_resolves_to_its_own_adapter(runner):
    # Hermes is now a first-class CLI adapter (hermes -z), not an MCP-only
    # fallback. It uses its own configured model, so the model is a placeholder.
    provider, _model = runner._resolve(_turn("hermes", None), _args())
    assert provider == "hermes"
    assert runner._ADAPTERS["hermes"].cli == "hermes"


def test_openclaw_resolves_to_its_own_adapter(runner):
    # OpenClaw is now a first-class CLI adapter (openclaw agent --message), not a
    # fallback. It uses its own configured model, so the model is a placeholder.
    provider, _model = runner._resolve(_turn("openclaw", None), _args())
    assert provider == "openclaw"
    assert runner._ADAPTERS["openclaw"].cli == "openclaw"


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


def test_new_payload_provider_field_is_preferred(runner):
    # The server's explicit per-turn `provider` field wins over model-prefix
    # guessing — the connector no longer has to infer the provider.
    turn = {"provider": "gemini", "model": "gemini-3.1-pro-preview"}
    assert runner._resolve(turn, _args()) == ("gemini", "gemini-3.1-pro-preview")


def test_old_payload_without_provider_field_still_resolves(runner):
    # An old server payload (no `provider` field) still works via model prefix.
    turn = {"model": "gpt-5.4-mini"}
    assert runner._resolve(turn, _args()) == ("openai", "gpt-5.4-mini")


def test_detect_providers_uses_cli_presence(runner, monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda cli: cli in {"claude", "gemini"})
    assert set(runner._detect_providers()) == {"claude", "gemini"}


# --- Hermes adapter (Path A: one-shot `hermes -z`, full state every turn) ---

class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _full_turn():
    return {
        "match_id": "M1",
        "static": {
            "your_agent_id": "you",
            "all_agent_ids": ["you", "rival"],
            "your_strategy": "win",
            "rules": "the rules",
        },
        "scoreboard": [{"agent_id": "you", "round_score": 0}],
        "history": [],
        "current": {"round": 1, "turn": 1, "phase": "act", "talk_messages": []},
    }


def test_hermes_adapter_is_sessionless_and_modelless(runner):
    a = runner._ADAPTERS["hermes"]
    assert a.cli == "hermes"
    assert a.supports_resume is False


def test_hermes_first_invokes_hermes_z_one_shot_no_model(runner, monkeypatch):
    calls = []
    monkeypatch.setattr(
        sys.modules["agentludum_connector"], "_run",
        lambda argv, **kw: calls.append(argv) or _FakeProc(
            stdout='{"action":"HELP","target_id":"rival","thinking":"t"}'
        ),
    )
    sess = runner._GameSession(provider="hermes", model="hermes")
    text, usage = runner._ADAPTERS["hermes"].first(
        body="BODY", framing="FRAME", model="ignored", session=sess
    )
    assert calls == [["hermes", "-z", "FRAME\n\nBODY"]]
    assert "--model" not in calls[0]
    assert usage is None
    assert sess.token is None  # Path A: no session captured
    import json
    assert json.loads(text)["action"] == "HELP"


def test_hermes_decide_sends_full_state_every_turn(runner, monkeypatch):
    prompts = []
    monkeypatch.setattr(
        sys.modules["agentludum_connector"], "_run",
        lambda argv, **kw: prompts.append(argv[-1]) or _FakeProc(
            stdout='{"action":"HOARD","target_id":null,"thinking":""}'
        ),
    )
    sess = runner._GameSession(provider="hermes", model="hermes")
    runner._decide(_full_turn(), sess)
    runner._decide(_full_turn(), sess)  # second turn
    # Both turns send the FULL setup body (no delta), and no session is captured.
    assert len(prompts) == 2
    assert all("GAME SO FAR" in p for p in prompts)
    assert sess.token is None


def test_hermes_malformed_output_yields_fallback_move(runner, monkeypatch):
    monkeypatch.setattr(
        sys.modules["agentludum_connector"], "_run", lambda argv, **kw: _FakeProc(stdout="not json at all")
    )
    sess = runner._GameSession(provider="hermes", model="hermes")
    move = runner._decide(_full_turn(), sess)
    assert move["is_connector_fallback"] is True
    assert move["action"] == "HOARD"  # the act-phase default


def test_detect_providers_includes_hermes(runner, monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda cli: cli in {"hermes", "claude"})
    assert set(runner._detect_providers()) == {"hermes", "claude"}


# --- OpenClaw adapter (Path A: one-shot `openclaw agent --message`) ---

def test_openclaw_adapter_is_sessionless_and_modelless(runner):
    a = runner._ADAPTERS["openclaw"]
    assert a.cli == "openclaw"
    assert a.supports_resume is False


def test_openclaw_first_invokes_one_shot_no_model(runner, monkeypatch):
    calls = []
    monkeypatch.setattr(
        sys.modules["agentludum_connector"], "_run",
        lambda argv, **kw: calls.append(argv) or _FakeProc(
            stdout='{"action":"HELP","target_id":"rival","thinking":"t"}'
        ),
    )
    sess = runner._GameSession(provider="openclaw", model="openclaw")
    text, usage = runner._ADAPTERS["openclaw"].first(
        body="BODY", framing="FRAME", model="ignored", session=sess
    )
    assert calls == [["openclaw", "agent", "--message", "FRAME\n\nBODY"]]
    assert "--model" not in calls[0]
    assert usage is None
    assert sess.token is None  # Path A: no session captured
    import json
    assert json.loads(text)["action"] == "HELP"


def test_openclaw_decide_sends_full_state_every_turn(runner, monkeypatch):
    prompts = []
    monkeypatch.setattr(
        sys.modules["agentludum_connector"], "_run",
        lambda argv, **kw: prompts.append(argv[-1]) or _FakeProc(
            stdout='{"action":"HOARD","target_id":null,"thinking":""}'
        ),
    )
    sess = runner._GameSession(provider="openclaw", model="openclaw")
    runner._decide(_full_turn(), sess)
    runner._decide(_full_turn(), sess)  # second turn
    assert len(prompts) == 2
    assert all("GAME SO FAR" in p for p in prompts)
    assert sess.token is None


def test_openclaw_malformed_output_yields_fallback_move(runner, monkeypatch):
    monkeypatch.setattr(
        sys.modules["agentludum_connector"], "_run", lambda argv, **kw: _FakeProc(stdout="not json at all")
    )
    sess = runner._GameSession(provider="openclaw", model="openclaw")
    move = runner._decide(_full_turn(), sess)
    assert move["is_connector_fallback"] is True
    assert move["action"] == "HOARD"


def test_detect_providers_includes_openclaw(runner, monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda cli: cli in {"openclaw", "claude"})
    assert set(runner._detect_providers()) == {"openclaw", "claude"}


# --- Service install (`--install`): one command sets up the background service ---

_SCRIPT_PATH = "/home/me/.agentludum/agentludum_connector.py"


def test_xml_escape_escapes_markup_chars(runner):
    assert runner._xml_escape("a&b<c>") == "a&amp;b&lt;c&gt;"


def test_macos_install_plan_writes_secure_plist_and_loads_it(runner):
    plan = runner._macos_install_plan(
        "/usr/bin/python3", _SCRIPT_PATH, "sk_conn_abc", "https://agentludum.com", "/Users/me", 501
    )
    (path, content, mode) = plan.files[0]
    assert path == "/Users/me/Library/LaunchAgents/com.agentludum.connector.plist"
    assert mode == 0o600  # the plist holds the key — keep it user-only
    assert "<key>RunAtLoad</key><true/>" in content
    assert "<key>KeepAlive</key><true/>" in content
    assert "sk_conn_abc" in content and "https://agentludum.com" in content
    # The SERVICE runs the connector WITHOUT --install (no install-loop in the daemon).
    assert "--install" not in content

    by_argv = {tuple(argv): allow for argv, allow in plan.commands}
    assert ("xattr", "-c", _SCRIPT_PATH) in by_argv  # clears provenance/quarantine
    assert ("launchctl", "enable", "gui/501/com.agentludum.connector") in by_argv
    # bootout is allowed to fail (idempotent re-install); bootstrap must succeed.
    assert by_argv[("launchctl", "bootout", "gui/501", path)] is True
    assert by_argv[("launchctl", "bootstrap", "gui/501", path)] is False


def test_linux_install_plan_writes_unit_with_restart_and_enables_it(runner):
    plan = runner._linux_install_plan(
        "/usr/bin/python3", _SCRIPT_PATH, "sk_conn_abc", "https://agentludum.com", "/home/me"
    )
    (path, content, mode) = plan.files[0]
    assert path == "/home/me/.config/systemd/user/agentludum-connector.service"
    assert mode == 0o600
    assert "Restart=on-failure" in content
    assert (
        f"ExecStart=/usr/bin/python3 {_SCRIPT_PATH} --key sk_conn_abc --url https://agentludum.com"
        in content
    )
    argvs = [argv for argv, _ in plan.commands]
    assert ["systemctl", "--user", "daemon-reload"] in argvs
    assert ["systemctl", "--user", "enable", "--now", "agentludum-connector.service"] in argvs


def test_windows_install_plan_uses_schtasks_and_warns_about_restart(runner):
    plan = runner._windows_install_plan(
        "C:\\Py\\python.exe", "C:\\Users\\me\\connector.py", "sk_conn_abc", "https://agentludum.com"
    )
    assert plan.files == []
    (argv, allow_fail) = plan.commands[0]
    assert argv[0] == "schtasks" and "/create" in argv and "AgentLudumConnector" in argv
    assert allow_fail is False
    assert plan.note  # warns Windows on-logon tasks don't auto-restart


def test_install_service_dispatches_by_platform(runner, monkeypatch):
    captured = {}
    monkeypatch.setattr(runner, "_run_install_plan", lambda plan: captured.update(plan=plan))
    monkeypatch.setattr(runner.platform, "system", lambda: "Linux")
    assert runner._install_service("sk_conn_abc", "https://agentludum.com") == 0
    assert any("systemctl" in argv for argv, _ in captured["plan"].commands)


def test_install_service_unsupported_platform_returns_error(runner, monkeypatch):
    monkeypatch.setattr(runner.platform, "system", lambda: "Plan9")
    assert runner._install_service("sk_conn_abc", "https://agentludum.com") == 1


def test_run_install_plan_writes_files_then_stops_on_required_failure(runner, tmp_path):
    target = tmp_path / "service.cfg"
    plan = runner._InstallPlan(
        files=[(str(target), "unit-body", 0o600)],
        commands=[(["false"], False)],  # a required command that exits non-zero
    )
    with pytest.raises(RuntimeError):
        runner._run_install_plan(plan)
    # The file is written before commands run, so it lands even though a command failed.
    assert target.read_text() == "unit-body"
    assert (target.stat().st_mode & 0o777) == 0o600


def test_run_install_plan_tolerates_allowed_failures(runner):
    plan = runner._InstallPlan(files=[], commands=[(["false"], True)])
    runner._run_install_plan(plan)  # allow_fail=True → no raise


def test_singleton_lock_blocks_a_second_run_with_the_same_key(runner, monkeypatch, tmp_path):
    if runner.os.name != "posix":
        pytest.skip("flock singleton lock is POSIX-only")
    monkeypatch.setenv("HOME", str(tmp_path))
    first = runner._acquire_singleton_lock("sk_conn_deadbeef")
    assert first is not None
    # A second connector with the SAME key must back off and exit.
    with pytest.raises(SystemExit):
        runner._acquire_singleton_lock("sk_conn_deadbeef")
    # A DIFFERENT key may run side by side.
    other = runner._acquire_singleton_lock("sk_conn_cafef00d")
    assert other is not None
    first.close()
    other.close()
