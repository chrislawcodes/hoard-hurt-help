"""Tests for the slug-scoped implement concurrency lock.

Covers:
- A second command_implement invocation is blocked (returns 1, no Codex) while
  the lockfile is held exclusively by another open fd.
- Closing the holder fd releases the lock so a subsequent invocation succeeds
  (no stale-lock deadlock after a crash).
- A single successful invocation acquires the lock, dispatches Codex (stubbed),
  and releases the lock so the file can be re-locked afterwards.
"""
import contextlib
import fcntl
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


SCRIPT_DIR = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SLUG = "impl-lock-test"

_CLEAN_STATUS = subprocess.CompletedProcess(
    args=["git", "status", "--porcelain"],
    returncode=0,
    stdout="",
    stderr="",
)

_ONE_SERIAL_GROUP = [
    {"parallel": False, "tasks": ["task 1"], "files": [], "overlap_warning": None}
]


def _noop_heartbeat() -> MagicMock:
    hb = MagicMock()
    hb.return_value.__enter__ = MagicMock(return_value=None)
    hb.return_value.__exit__ = MagicMock(return_value=False)
    return hb


class ImplementLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factory_state = _load("factory_state")
        self.factory_cmd_implement = _load("factory_cmd_implement")
        self.run_factory = _load("run_factory")

        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

        self._runs_root = Path(self._tmpdir.name) / "docs" / "workflow" / "feature-runs"
        self._runs_patch = patch.object(
            self.factory_state, "FACTORY_RUNS_ROOT", self._runs_root
        )
        self._runs_patch.start()
        self.addCleanup(self._runs_patch.stop)

        self._setup_state()

    def _setup_state(self) -> None:
        wdir = self.factory_state.workflow_dir(SLUG)
        wdir.mkdir(parents=True, exist_ok=True)
        state = self.factory_state._default_workflow_state()
        state["schema_version"] = 2
        self.factory_state.atomic_json_write(
            self.factory_state.factory_state_path(SLUG), state
        )

    def _lock_path(self) -> Path:
        return self.factory_state.workflow_dir(SLUG) / ".implement.lock"

    def _invoke(self, serial_rc: int = 0) -> tuple[int, str, str]:
        """Run command_implement with all external calls stubbed."""
        parser = self.run_factory.build_parser()
        args = parser.parse_args(["implement", "--slug", SLUG])

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        with (
            patch.object(
                self.factory_cmd_implement.subprocess,
                "run",
                return_value=_CLEAN_STATUS,
            ),
            patch.object(
                self.factory_cmd_implement,
                "parse_parallel_task_groups",
                return_value=_ONE_SERIAL_GROUP,
            ),
            patch.object(
                self.factory_cmd_implement,
                "_run_serial",
                return_value=serial_rc,
            ) as mock_serial,
            patch.object(
                self.factory_cmd_implement,
                "HeartbeatEmitter",
                _noop_heartbeat(),
            ),
            contextlib.redirect_stdout(stdout_buf),
            contextlib.redirect_stderr(stderr_buf),
        ):
            self._last_mock_serial = mock_serial
            rc = args.func(args)

        return rc, stdout_buf.getvalue(), stderr_buf.getvalue()

    def test_blocked_while_lock_held_returns_one_no_codex(self) -> None:
        """A second implement invocation fails immediately when the lock is held."""
        lock_path = self._lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(holder_fd, fcntl.LOCK_EX)

            rc, _stdout, stderr = self._invoke()

            self.assertEqual(rc, 1)
            self.assertIn("already running", stderr)
            self.assertIn(SLUG, stderr)
            self._last_mock_serial.assert_not_called()
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

    def test_no_stale_lock_after_holder_closes_fd(self) -> None:
        """Closing the holder fd releases the lock; a retry can then proceed."""
        lock_path = self._lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(holder_fd, fcntl.LOCK_EX)

        rc_blocked, _so, stderr = self._invoke()
        self.assertEqual(rc_blocked, 1, "should be blocked while lock is held")
        self.assertIn("already running", stderr)

        # Simulate crash / process death by closing the fd without explicit LOCK_UN.
        # The OS releases the lock automatically.
        os.close(holder_fd)

        rc_retry, _so2, stderr2 = self._invoke()
        self.assertEqual(rc_retry, 0, f"should succeed after lock released; stderr={stderr2!r}")
        self._last_mock_serial.assert_called_once()

    def test_single_successful_run_acquires_and_releases_lock(self) -> None:
        """A single implement run succeeds and leaves the lockfile re-lockable."""
        rc, _stdout, stderr = self._invoke()

        self.assertEqual(rc, 0, f"unexpected failure; stderr={stderr!r}")
        self._last_mock_serial.assert_called_once()

        # Lock must be released after the run — a new LOCK_NB acquire must succeed.
        lock_path = self._lock_path()
        self.assertTrue(lock_path.exists(), "lockfile should exist after a run")
        verify_fd = os.open(str(lock_path), os.O_RDWR, 0)
        try:
            fcntl.flock(verify_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Lock acquired — no exception, so it was released correctly.
        finally:
            fcntl.flock(verify_fd, fcntl.LOCK_UN)
            os.close(verify_fd)

    def test_lockfile_contains_pid_and_slug(self) -> None:
        """The lockfile records pid, slug, and started_at while the lock is held."""
        captured: dict = {}

        original_acquire = self.factory_cmd_implement._acquire_implement_lock

        def _capturing_acquire(slug: str) -> tuple[int, str]:
            fd, err = original_acquire(slug)
            if fd != -1:
                os.lseek(fd, 0, os.SEEK_SET)
                raw = os.read(fd, 4096)
                captured.update(json.loads(raw.decode("utf-8")))
            return fd, err

        with patch.object(
            self.factory_cmd_implement,
            "_acquire_implement_lock",
            side_effect=_capturing_acquire,
        ):
            rc, _stdout, stderr = self._invoke()

        self.assertEqual(rc, 0)
        self.assertEqual(captured.get("slug"), SLUG)
        self.assertEqual(captured.get("pid"), os.getpid())
        self.assertIn("started_at", captured)

    def test_blocked_error_message_includes_holder_pid(self) -> None:
        """The "already running" message includes the holder's pid."""
        lock_path = self._lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(holder_fd, fcntl.LOCK_EX)
            payload = json.dumps(
                {"pid": 99999, "started_at": "2026-01-01T00:00:00+00:00", "slug": SLUG}
            ).encode("utf-8")
            os.ftruncate(holder_fd, 0)
            os.lseek(holder_fd, 0, os.SEEK_SET)
            os.write(holder_fd, payload)

            rc, _stdout, stderr = self._invoke()

            self.assertEqual(rc, 1)
            self.assertIn("99999", stderr)
            self.assertIn("2026-01-01", stderr)
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)


if __name__ == "__main__":
    unittest.main()
