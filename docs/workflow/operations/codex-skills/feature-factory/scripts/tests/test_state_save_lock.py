"""save_workflow_state must take the state lock (heartbeat-race fix).

The heartbeat thread persists state under with_locked_state while running
concurrently in the same process as the command that spawned it. If the full
overwrite (save_workflow_state) bypassed the lock, it could land in the middle of
the heartbeat's locked read-modify-write and drop a real field. These tests pin
that save_workflow_state now acquires the same exclusive lock.
"""
from __future__ import annotations

import fcntl
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "factory_state.py"
SPEC = importlib.util.spec_from_file_location("factory_state", SCRIPT_PATH)
assert SPEC and SPEC.loader
FS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FS
SPEC.loader.exec_module(FS)


class SaveWorkflowStateLockTests(unittest.TestCase):
    def test_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(FS, "FACTORY_RUNS_ROOT", Path(tmpdir)):
                slug = "save-round-trip"
                state = FS._default_workflow_state()
                state["marker"] = "saved"
                FS.save_workflow_state(slug, state)
                on_disk = json.loads(FS.factory_state_path(slug).read_text(encoding="utf-8"))
                self.assertEqual(on_disk["marker"], "saved")

    def test_blocks_when_lock_is_held(self) -> None:
        # Hold the exclusive flock on the state file from a separate descriptor,
        # then assert save_workflow_state cannot proceed and times out — proving
        # it acquires the same lock. time.sleep is patched out so the 11 retries
        # are fast.
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(FS, "FACTORY_RUNS_ROOT", Path(tmpdir)):
                slug = "save-contended"
                path = FS.factory_state_path(slug)
                FS.atomic_json_write(path, FS._default_workflow_state())
                with path.open("r+", encoding="utf-8") as holder:
                    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
                    try:
                        with patch.object(FS.time, "sleep", lambda *_a, **_k: None):
                            with self.assertRaises(TimeoutError):
                                FS.save_workflow_state(slug, FS._default_workflow_state())
                    finally:
                        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    unittest.main()
