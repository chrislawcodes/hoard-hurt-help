"""Tests for implement's fail-fast wiring to the shared codex watchdog.

Locks in the incident fix: a transient idle stall is retried once and, if it
clears, the slice succeeds; a persistent stall fails the slice fast instead of
hanging on a 60-minute wall.
"""
import contextlib
import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_cmd_implement as IMPL  # noqa: E402


def _passthrough_record(*args, **kwargs):
    # record_ai_call(slug, stage, round, activity_type, model, callable_fn, ...)
    return args[5]()


class ClassifyRcTests(unittest.TestCase):
    def test_stall_sentinels_become_failure(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(IMPL._classify_codex_rc(IMPL.RC_IDLE_TIMEOUT), 1)
            self.assertEqual(IMPL._classify_codex_rc(IMPL.RC_HARD_TIMEOUT), 1)
            self.assertEqual(IMPL._classify_codex_rc(IMPL.RC_NOT_FOUND), 1)

    def test_real_codes_pass_through(self) -> None:
        self.assertEqual(IMPL._classify_codex_rc(0), 0)
        self.assertEqual(IMPL._classify_codex_rc(7), 7)


class RunSerialRetryTests(unittest.TestCase):
    def _run(self, rc_sequence: list[int]) -> tuple[int, int]:
        calls = {"n": 0}

        def _fake_cmd(command, cwd, *, slug, index):
            i = calls["n"]
            calls["n"] += 1
            return subprocess.CompletedProcess(command, rc_sequence[i], "", "")

        with (
            patch.object(IMPL, "_build_codex_prompt", return_value="prompt"),
            patch.object(IMPL, "record_ai_call", side_effect=_passthrough_record),
            patch.object(IMPL, "revert_protected_files"),
            patch.object(IMPL, "_implementation_round", return_value=0),
            patch.object(IMPL, "_run_codex_command", side_effect=_fake_cmd),
            # Completion gate seams: these tests exercise the retry wiring only,
            # so the pre-dispatch HEAD capture and the post-dispatch slice-
            # completion verification are stubbed out.
            patch.object(IMPL, "_git_head_sha", return_value="base-sha"),
            patch.object(IMPL, "_slice_completion_error", return_value=None),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            rc = IMPL._run_serial("slug", ["task"])
        return rc, calls["n"]

    def test_idle_then_success_is_retried_and_succeeds(self) -> None:
        rc, n = self._run([IMPL.RC_IDLE_TIMEOUT, 0])
        self.assertEqual(rc, 0)
        self.assertEqual(n, 2)  # retried once, second attempt cleared

    def test_persistent_idle_fails_fast_after_retries(self) -> None:
        rc, n = self._run([IMPL.RC_IDLE_TIMEOUT, IMPL.RC_IDLE_TIMEOUT])
        self.assertEqual(rc, 1)  # classified as failure, not a hang
        self.assertEqual(n, 2)  # bounded attempts, default max 2

    def test_clean_success_runs_once(self) -> None:
        rc, n = self._run([0])
        self.assertEqual(rc, 0)
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
