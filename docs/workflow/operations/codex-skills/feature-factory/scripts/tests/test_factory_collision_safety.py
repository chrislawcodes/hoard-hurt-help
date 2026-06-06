"""Collision-safety: the shared per-slug run lock, the autopilot lock, and the
primary-checkout worktree warning.
"""
import argparse
import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import factory_runlock as rl  # noqa: E402
import factory_git as git  # noqa: E402
import factory_state as st  # noqa: E402
import factory_cmd_autopilot as ap  # noqa: E402


class RunLockTests(unittest.TestCase):
    def test_second_acquire_blocks_then_release_frees(self) -> None:
        slug = "rl-block-test"
        st.workflow_dir(slug).mkdir(parents=True, exist_ok=True)
        fd, err = rl.acquire_run_lock(slug, "implement", "implement")
        self.assertNotEqual(fd, -1)
        self.assertEqual(err, "")
        try:
            fd2, err2 = rl.acquire_run_lock(slug, "implement", "implement")
            self.assertEqual(fd2, -1)
            self.assertIn("already running", err2)
            self.assertIn("implement", err2)
        finally:
            rl.release_run_lock(fd)
        # after release, re-acquire succeeds
        fd3, err3 = rl.acquire_run_lock(slug, "implement", "implement")
        self.assertNotEqual(fd3, -1)
        rl.release_run_lock(fd3)

    def test_different_lock_names_are_independent(self) -> None:
        slug = "rl-names-test"
        st.workflow_dir(slug).mkdir(parents=True, exist_ok=True)
        fd_a, _ = rl.acquire_run_lock(slug, "implement", "implement")
        fd_b, err_b = rl.acquire_run_lock(slug, "autopilot", "autopilot")
        try:
            self.assertNotEqual(fd_a, -1)
            self.assertNotEqual(fd_b, -1)  # different lock name → no contention
            self.assertEqual(err_b, "")
        finally:
            rl.release_run_lock(fd_a)
            rl.release_run_lock(fd_b)


class AutopilotLockTests(unittest.TestCase):
    def test_autopilot_refuses_when_its_lock_is_held(self) -> None:
        slug = "ap-lock-test"
        st.workflow_dir(slug).mkdir(parents=True, exist_ok=True)
        fd, _ = rl.acquire_run_lock(slug, "autopilot", "autopilot")
        try:
            args = argparse.Namespace(slug=slug, max_iterations=1)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = ap.command_autopilot(args)
            self.assertEqual(rc, 1)
            self.assertIn("already running", err.getvalue())
        finally:
            rl.release_run_lock(fd)


class WorktreeWarningTests(unittest.TestCase):
    def _git_output(self, mapping):
        return lambda *args: mapping.get(args[-1])

    def test_is_linked_worktree_true_when_dirs_differ(self) -> None:
        with mock.patch.object(git, "git_output", self._git_output(
            {"--git-dir": "/r/.git/worktrees/x", "--git-common-dir": "/r/.git"})):
            self.assertTrue(git.is_linked_worktree())

    def test_is_linked_worktree_false_in_primary(self) -> None:
        with mock.patch.object(git, "git_output", self._git_output(
            {"--git-dir": "/r/.git", "--git-common-dir": "/r/.git"})):
            self.assertFalse(git.is_linked_worktree())

    def test_warn_silenced_by_env(self) -> None:
        with mock.patch.dict(os.environ, {"FF_ALLOW_PRIMARY_CHECKOUT": "1"}), \
             mock.patch.object(git, "is_linked_worktree", return_value=False):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                git.warn_if_primary_checkout()
            self.assertEqual(err.getvalue(), "")

    def test_warn_fires_in_primary_checkout(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(git, "is_linked_worktree", return_value=False):
            os.environ.pop("FF_ALLOW_PRIMARY_CHECKOUT", None)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                git.warn_if_primary_checkout()
            self.assertIn("PRIMARY checkout", err.getvalue())
            self.assertIn("git worktree add", err.getvalue())

    def test_warn_silent_in_linked_worktree(self) -> None:
        with mock.patch.object(git, "is_linked_worktree", return_value=True):
            os.environ.pop("FF_ALLOW_PRIMARY_CHECKOUT", None)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                git.warn_if_primary_checkout()
            self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
