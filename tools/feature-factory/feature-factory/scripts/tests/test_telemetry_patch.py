import subprocess
import sys
import threading
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_telemetry_commands as TC  # noqa: E402


class TelemetryPatchTests(unittest.TestCase):
    """Regression coverage for the subprocess-counting monkeypatch.

    The original design saved the real subprocess.run in thread-local storage while
    patching the process-global, so a subprocess call from any non-owner thread
    recursed forever (RecursionError). slug=None skips the state write.
    """

    def test_cross_thread_subprocess_does_not_recurse(self) -> None:
        outcome: dict[str, object] = {}
        with TC.command_telemetry_scope(None, "checkpoint", "spec"):
            def worker() -> None:
                try:
                    subprocess.run(["true"], capture_output=True)
                    outcome["ok"] = True
                except Exception as exc:  # noqa: BLE001
                    outcome["err"] = type(exc).__name__

            t = threading.Thread(target=worker)
            t.start()
            t.join()
        self.assertTrue(outcome.get("ok"), outcome)

    def test_owner_thread_subprocess_is_counted(self) -> None:
        with TC.command_telemetry_scope(None, "checkpoint", "spec") as ctx:
            subprocess.run(["true"], capture_output=True)
            self.assertGreaterEqual(ctx.subprocess_invocations, 1)

    def test_patch_restored_after_scope(self) -> None:
        before_run, before_popen = subprocess.run, subprocess.Popen
        with TC.command_telemetry_scope(None, "x", None):
            self.assertIsNot(subprocess.run, before_run)  # patched while active
        self.assertIs(subprocess.run, before_run)
        self.assertIs(subprocess.Popen, before_popen)

    def test_nested_scopes_install_once_and_restore_on_outermost(self) -> None:
        before_run = subprocess.run
        with TC.command_telemetry_scope(None, "outer", None):
            patched = subprocess.run
            self.assertIsNot(patched, before_run)
            with TC.command_telemetry_scope(None, "inner", None):
                self.assertIs(subprocess.run, patched)  # same patch, not double-wrapped
            self.assertIs(subprocess.run, patched)  # still patched: outer active
        self.assertIs(subprocess.run, before_run)  # restored only at outermost exit


if __name__ == "__main__":
    unittest.main()
