import argparse
import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


FACTORY_STATE = _load("factory_state")
CHECK_WORKFLOW_ISOLATION = _load("check_workflow_isolation")


class CheckWorkflowIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_root = Path(self._tmpdir.name)
        self.runs_root = self.repo_root / "docs" / "workflow" / "feature-runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo_root, check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        self._old_cwd = Path.cwd()
        os.chdir(self.repo_root)
        self.addCleanup(lambda: os.chdir(self._old_cwd))

        self._repo_patch = mock.patch.object(FACTORY_STATE, "REPO_ROOT", self.repo_root)
        self._runs_patch = mock.patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", self.runs_root)
        self._cmd_repo_patch = mock.patch.object(CHECK_WORKFLOW_ISOLATION, "REPO_ROOT", self.repo_root)
        self._repo_patch.start()
        self._runs_patch.start()
        self._cmd_repo_patch.start()
        self.addCleanup(self._repo_patch.stop)
        self.addCleanup(self._runs_patch.stop)
        self.addCleanup(self._cmd_repo_patch.stop)

    def _invoke(self, args: argparse.Namespace) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = CHECK_WORKFLOW_ISOLATION.command_check_workflow_isolation(args)
        return rc, stdout.getvalue(), stderr.getvalue()

    def _baseline_path(self, name: str) -> Path:
        return self.repo_root / f"{name}.json"

    def test_capture_and_check_with_no_changes_pass(self) -> None:
        baseline = self._baseline_path("baseline-clean")
        rc, _, _ = self._invoke(argparse.Namespace(capture_baseline=baseline, check=False, baseline=None))
        self.assertEqual(rc, 0)

        rc, _, _ = self._invoke(argparse.Namespace(capture_baseline=None, check=True, baseline=baseline))
        self.assertEqual(rc, 0)

    def test_new_file_under_workflow_root_fails_check(self) -> None:
        baseline = self._baseline_path("baseline-dirty")
        self._invoke(argparse.Namespace(capture_baseline=baseline, check=False, baseline=None))

        dirty_file = self.runs_root / "dirty.txt"
        dirty_file.write_text("dirty\n", encoding="utf-8")

        rc, _, stderr = self._invoke(argparse.Namespace(capture_baseline=None, check=True, baseline=baseline))
        self.assertEqual(rc, 1)
        self.assertIn(str(dirty_file.relative_to(self.repo_root)), stderr)

    def test_preexisting_dirty_file_is_allowed_when_in_baseline(self) -> None:
        dirty_file = self.runs_root / "baseline-ok.txt"
        dirty_file.write_text("baseline\n", encoding="utf-8")
        baseline = self._baseline_path("baseline-preexisting")
        self._invoke(argparse.Namespace(capture_baseline=baseline, check=False, baseline=None))

        rc, _, _ = self._invoke(argparse.Namespace(capture_baseline=None, check=True, baseline=baseline))
        self.assertEqual(rc, 0)

    def test_removed_dirty_file_fails_check(self) -> None:
        dirty_file = self.runs_root / "removed.txt"
        dirty_file.write_text("remove me\n", encoding="utf-8")
        baseline = self._baseline_path("baseline-removed")
        self._invoke(argparse.Namespace(capture_baseline=baseline, check=False, baseline=None))

        dirty_file.unlink()

        rc, _, stderr = self._invoke(argparse.Namespace(capture_baseline=None, check=True, baseline=baseline))
        self.assertEqual(rc, 1)
        self.assertIn(str(dirty_file.relative_to(self.repo_root)), stderr)


if __name__ == "__main__":
    unittest.main()
