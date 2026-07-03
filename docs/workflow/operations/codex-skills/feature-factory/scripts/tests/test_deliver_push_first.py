"""Tests for factory_deliver.ensure_branch_pushed (deliver push-first).

Postmortems strategy-first-onboarding and dedup-engine-cseries both hit
``deliver --create-pr`` failing because the branch was never pushed. The fix:
publish the branch (``git push -u origin HEAD``) before ``gh pr create`` when
it has no upstream or is ahead — and hard-stop with a rebase instruction when
HEAD is behind origin/main (never auto-rebase).

No test here runs real git push: git queries and the push subprocess are
stubbed at the module boundary, matching test_factory_cmd_deliver.py.
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

import factory_deliver as FACTORY_DELIVER  # noqa: E402


BRANCH = "feature/x"
_BEHIND_MAIN_ARGS = ("rev-list", "--count", "HEAD..origin/main")
_AHEAD_UPSTREAM_ARGS = ("rev-list", "--count", "@{upstream}..HEAD")


def _fake_git_output(behind_main: str | None, ahead_upstream: str | None):
    def fake(*args: str) -> str | None:
        if args == _BEHIND_MAIN_ARGS:
            return behind_main
        if args == _AHEAD_UPSTREAM_ARGS:
            return ahead_upstream
        raise AssertionError(f"unexpected git_output args: {args}")
    return fake


class EnsureBranchPushedTests(unittest.TestCase):
    def _call(
        self,
        upstream: str | None,
        *,
        behind_main: str | None = "0",
        ahead: str | None = None,
        dry_run: bool = False,
        push_rc: int = 0,
        push_stderr: str = "",
    ) -> tuple[str | None, list[list[str]], str]:
        """Run ensure_branch_pushed with stubbed git. Returns (result, pushes, stdout)."""
        pushes: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            pushes.append(list(cmd))
            return subprocess.CompletedProcess(cmd, push_rc, stdout="", stderr=push_stderr)

        self._pushes = pushes
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(
                FACTORY_DELIVER, "git_output", side_effect=_fake_git_output(behind_main, ahead)
            ))
            stack.enter_context(patch.object(
                FACTORY_DELIVER.subprocess, "run", side_effect=fake_run
            ))
            stack.enter_context(patch.object(
                FACTORY_DELIVER, "upstream_branch_name", return_value=f"origin/{BRANCH}"
            ))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = FACTORY_DELIVER.ensure_branch_pushed(BRANCH, upstream, dry_run=dry_run)
        return result, pushes, stdout.getvalue()

    def test_blocks_when_behind_origin_main(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self._call(None, behind_main="2")
        message = str(ctx.exception)
        self.assertIn("behind origin/main", message)
        self.assertIn("git fetch origin main && git rebase origin/main", message)
        self.assertIn("never rebases automatically", message)
        self.assertEqual(self._pushes, [])

    def test_pushes_when_no_upstream(self) -> None:
        result, pushes, stdout = self._call(None)
        self.assertEqual(len(pushes), 1)
        cmd = pushes[0]
        self.assertEqual(cmd[:2], ["git", "-C"])
        self.assertEqual(cmd[3:], ["push", "-u", "origin", "HEAD"])
        self.assertEqual(result, f"origin/{BRANCH}")
        self.assertIn("pushed branch", stdout)

    def test_pushes_when_ahead_of_upstream(self) -> None:
        result, pushes, _ = self._call(f"origin/{BRANCH}", ahead="3")
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0][3:], ["push", "-u", "origin", "HEAD"])
        self.assertEqual(result, f"origin/{BRANCH}")

    def test_no_push_when_up_to_date_with_upstream(self) -> None:
        result, pushes, _ = self._call(f"origin/{BRANCH}", ahead="0")
        self.assertEqual(pushes, [])
        self.assertEqual(result, f"origin/{BRANCH}")

    def test_no_push_when_ahead_count_unknown(self) -> None:
        """Fail open on an unreadable ahead-count for an existing upstream."""
        result, pushes, _ = self._call(f"origin/{BRANCH}", ahead=None)
        self.assertEqual(pushes, [])
        self.assertEqual(result, f"origin/{BRANCH}")

    def test_behind_count_unknown_fails_open(self) -> None:
        """No origin/main ref (count unreadable) must not block the push."""
        _, pushes, _ = self._call(None, behind_main=None)
        self.assertEqual(len(pushes), 1)

    def test_dry_run_skips_push(self) -> None:
        result, pushes, stdout = self._call(None, dry_run=True)
        self.assertEqual(pushes, [])
        self.assertIsNone(result)
        self.assertIn("dry-run: would push branch", stdout)

    def test_push_failure_raises_with_detail(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self._call(None, push_rc=1, push_stderr="remote: permission denied")
        message = str(ctx.exception)
        self.assertIn("could not push branch", message)
        self.assertIn("remote: permission denied", message)


if __name__ == "__main__":
    unittest.main()
