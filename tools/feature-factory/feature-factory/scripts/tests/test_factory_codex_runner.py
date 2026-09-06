"""Tests for the shared codex runner's idle/no-output watchdog.

A stalled ``codex exec`` is simulated with a fake command (the Python
interpreter running a tiny script) so the watchdog can be exercised for real:
- a command that sleeps without printing -> idle timeout fires and it is killed
- a command that keeps printing -> idle clock keeps resetting, runs to completion
- a command that prints forever -> the overall hard cap fires
- a command not on PATH -> fast rejection, nothing launched
- the retry helper retries an idle stall but not a hard timeout
"""
import io
import sys
import time
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_codex_runner as RUNNER  # noqa: E402

PY = sys.executable


def _completed(returncode: int):
    import subprocess
    return subprocess.CompletedProcess(["codex"], returncode, stdout="", stderr="")


class RunCodexWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(__file__).resolve().parent
        # Quiet the per-60s liveness line in tests.
        self._kwargs = {"status_interval": 9999.0}

    def test_healthy_command_returns_zero_and_captures_output(self) -> None:
        result = RUNNER.run_codex(
            [PY, "-u", "-c", "print('hello'); print('world')"],
            SCRIPT_DIR,
            idle_timeout=5.0,
            hard_timeout=30.0,
            **self._kwargs,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello", result.stdout)
        self.assertIn("world", result.stdout)

    def test_idle_stall_is_killed_with_idle_sentinel(self) -> None:
        start = time.monotonic()
        result = RUNNER.run_codex(
            [PY, "-u", "-c", "import time; time.sleep(30)"],
            SCRIPT_DIR,
            idle_timeout=0.5,
            hard_timeout=30.0,
            **self._kwargs,
        )
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, RUNNER.RC_IDLE_TIMEOUT)
        # Killed fast — nowhere near the 30s the command would have slept.
        self.assertLess(elapsed, 10.0)

    def test_idle_kill_leaves_no_orphan_child(self) -> None:
        marker = self._tmp / "orphan-marker.txt"
        marker.unlink(missing_ok=True)
        script = "import time, sys; time.sleep(10); open(sys.argv[1], 'w').write('alive')"
        result = RUNNER.run_codex(
            [PY, "-u", "-c", script, str(marker)],
            SCRIPT_DIR,
            idle_timeout=0.5,
            hard_timeout=30.0,
            **self._kwargs,
        )
        self.assertEqual(result.returncode, RUNNER.RC_IDLE_TIMEOUT)
        # The child was killed before it could finish sleeping and write the
        # marker; give it a moment and confirm it never did (i.e. not orphaned).
        time.sleep(0.5)
        self.addCleanup(lambda: marker.unlink(missing_ok=True))
        self.assertFalse(marker.exists(), "child survived the kill — orphaned process")

    def test_steady_output_keeps_idle_clock_alive(self) -> None:
        # Prints every 0.2s for ~1.4s then exits cleanly; with a 1.0s idle window
        # the output must keep resetting the clock so it is NOT killed.
        script = "import time\nfor _ in range(7):\n    print('tick', flush=True)\n    time.sleep(0.2)"
        result = RUNNER.run_codex(
            [PY, "-u", "-c", script],
            SCRIPT_DIR,
            idle_timeout=1.0,
            hard_timeout=30.0,
            **self._kwargs,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.count("tick"), 7)

    def test_hard_timeout_fires_even_with_continuous_output(self) -> None:
        # Never idle (prints constantly), but the overall cap still stops it.
        script = "import time\nwhile True:\n    print('x', flush=True)\n    time.sleep(0.05)"
        start = time.monotonic()
        result = RUNNER.run_codex(
            [PY, "-u", "-c", script],
            SCRIPT_DIR,
            idle_timeout=30.0,
            hard_timeout=0.5,
            **self._kwargs,
        )
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, RUNNER.RC_HARD_TIMEOUT)
        self.assertLess(elapsed, 10.0)

    def test_missing_executable_returns_not_found_without_launching(self) -> None:
        result = RUNNER.run_codex(
            ["this-binary-does-not-exist-xyz", "exec"],
            SCRIPT_DIR,
            **self._kwargs,
        )
        self.assertEqual(result.returncode, RUNNER.RC_NOT_FOUND)
        self.assertIn("not found on PATH", result.stderr)

    def test_log_path_receives_output(self) -> None:
        log_path = self._tmp / "runner-test.log"
        log_path.unlink(missing_ok=True)
        self.addCleanup(lambda: log_path.unlink(missing_ok=True))
        result = RUNNER.run_codex(
            [PY, "-u", "-c", "print('logged line')"],
            SCRIPT_DIR,
            idle_timeout=5.0,
            hard_timeout=30.0,
            log_path=log_path,
            **self._kwargs,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("logged line", log_path.read_text(encoding="utf-8"))


class RunCodexRetryTests(unittest.TestCase):
    def test_idle_stall_is_retried_once_then_succeeds(self) -> None:
        calls = {"n": 0}

        def _dispatch():
            calls["n"] += 1
            return _completed(RUNNER.RC_IDLE_TIMEOUT if calls["n"] == 1 else 0)

        buf = io.StringIO()
        import contextlib
        with contextlib.redirect_stderr(buf):
            result = RUNNER.run_codex_with_retry(_dispatch, max_attempts=2, label="t")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls["n"], 2)

    def test_idle_stall_gives_up_after_max_attempts(self) -> None:
        calls = {"n": 0}

        def _dispatch():
            calls["n"] += 1
            return _completed(RUNNER.RC_IDLE_TIMEOUT)

        import contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            result = RUNNER.run_codex_with_retry(_dispatch, max_attempts=2, label="t")
        self.assertEqual(result.returncode, RUNNER.RC_IDLE_TIMEOUT)
        self.assertEqual(calls["n"], 2)

    def test_hard_timeout_is_not_retried(self) -> None:
        calls = {"n": 0}

        def _dispatch():
            calls["n"] += 1
            return _completed(RUNNER.RC_HARD_TIMEOUT)

        result = RUNNER.run_codex_with_retry(_dispatch, max_attempts=3, label="t")
        self.assertEqual(result.returncode, RUNNER.RC_HARD_TIMEOUT)
        self.assertEqual(calls["n"], 1)

    def test_success_runs_once(self) -> None:
        calls = {"n": 0}

        def _dispatch():
            calls["n"] += 1
            return _completed(0)

        result = RUNNER.run_codex_with_retry(_dispatch, max_attempts=3, label="t")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
