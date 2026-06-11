"""Unit tests for codex_stream_runner.run_with_idle_watchdog.

The watchdog replaces a fixed capture-and-wait timeout. It must:
  a. Return full stdout/stderr and the real returncode when a command exits
     normally.
  b. Idle-kill (returncode 124) a command that goes *silent* longer than the
     idle window -- and do it quickly, not after the command's own long sleep.
  c. Ceiling-kill (returncode 124) a command that stays noisy (keeps resetting
     the idle timer) but never finishes.
  d. Preserve partial output captured before a kill, plus a `[watchdog] killed`
     marker explaining why it stopped.
"""
import io
import sys
import time
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import codex_stream_runner  # noqa: E402
from codex_stream_runner import run_with_idle_watchdog  # noqa: E402

_QUIET = {"heartbeat_interval_seconds": 999, "poll_interval_seconds": 0.05}


def _child(body: str) -> list[str]:
    # -u: unbuffered, so the watchdog sees each line promptly.
    return [sys.executable, "-u", "-c", body]


class RunWithIdleWatchdogTests(unittest.TestCase):
    def test_normal_exit_returns_full_output_and_returncode(self) -> None:
        result = run_with_idle_watchdog(
            _child("import sys; print('hello'); print('world'); sys.stderr.write('progress\\n')"),
            hard_ceiling_seconds=10,
            idle_timeout_seconds=5,
            heartbeat_stream=io.StringIO(),
            **_QUIET,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello", result.stdout)
        self.assertIn("world", result.stdout)
        self.assertIn("progress", result.stderr)
        self.assertNotIn("[watchdog] killed", result.stderr)

    def test_nonzero_exit_is_passed_through(self) -> None:
        result = run_with_idle_watchdog(
            _child("import sys; print('boom'); sys.exit(7)"),
            hard_ceiling_seconds=10,
            idle_timeout_seconds=5,
            heartbeat_stream=io.StringIO(),
            **_QUIET,
        )
        self.assertEqual(result.returncode, 7)
        self.assertIn("boom", result.stdout)

    def test_idle_kill_fires_fast_on_silence(self) -> None:
        start = time.monotonic()
        result = run_with_idle_watchdog(
            # Print once, then go silent for far longer than the idle window.
            _child("import time, sys; print('starting'); sys.stdout.flush(); time.sleep(30)"),
            hard_ceiling_seconds=30,
            idle_timeout_seconds=1,
            heartbeat_stream=io.StringIO(),
            **_QUIET,
        )
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, codex_stream_runner.TIMED_OUT_RETURNCODE)
        self.assertIn("starting", result.stdout)
        self.assertIn("idle watchdog", result.stderr)
        self.assertIn("[watchdog] killed", result.stderr)
        # Killed shortly after the 1s idle window -- not after the child's 30s sleep.
        self.assertLess(elapsed, 10)

    def test_ceiling_kill_fires_when_busy_but_never_finishing(self) -> None:
        start = time.monotonic()
        result = run_with_idle_watchdog(
            # Continuously noisy (resets idle every 0.1s) but never exits.
            _child("import time, sys\nwhile True:\n    print('tick'); sys.stdout.flush(); time.sleep(0.1)"),
            hard_ceiling_seconds=1,
            idle_timeout_seconds=30,
            heartbeat_stream=io.StringIO(),
            **_QUIET,
        )
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, codex_stream_runner.TIMED_OUT_RETURNCODE)
        self.assertIn("hard ceiling", result.stderr)
        self.assertIn("tick", result.stdout)
        self.assertLess(elapsed, 10)

    def test_heartbeat_emits_proof_of_life(self) -> None:
        sink = io.StringIO()
        run_with_idle_watchdog(
            _child("import time, sys; print('reading app/main.py'); sys.stdout.flush(); time.sleep(0.6)"),
            hard_ceiling_seconds=10,
            idle_timeout_seconds=5,
            heartbeat_interval_seconds=0,  # emit on the first poll
            poll_interval_seconds=0.05,
            heartbeat_stream=sink,
            label="codex spec/feasibility",
        )
        out = sink.getvalue()
        self.assertIn("codex spec/feasibility", out)
        self.assertIn("active:", out)


if __name__ == "__main__":
    unittest.main()
