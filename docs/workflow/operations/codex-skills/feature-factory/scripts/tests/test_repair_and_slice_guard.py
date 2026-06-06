"""Regression tests for the rebase/repair state-drift fixes (Batch 1).

- Bug 1: `repair` re-points a stale diff to current HEAD but must NOT advance
  the slice index (advancing skips an un-built slice).
- Bug 2: `implement` must refuse to dispatch a slice when the prior slice's
  declared files don't exist on disk (index drifted past an un-built slice).

The scripts dir is put on sys.path by the package conftest.py.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import factory_review as FR
import factory_parallel as FP


class AdvanceCheckpointRepointTests(unittest.TestCase):
    """Bug 1: repoint_only must keep the slice index; normal advance bumps it."""

    def _run(self, *, repoint_only: bool, start_index: int = 1) -> dict:
        captured: dict = {}

        def fake_update(_slug: str, fn) -> None:
            state: dict = {}
            fn(state)
            captured.update(state)

        with patch.object(FR, "parse_checkpoint_markers", return_value=(3, "markers-sha")), \
             patch.object(
                 FR, "checkpoint_progress_state",
                 return_value={"index": start_index, "markers_sha": "old", "last_diff_head_sha": "OLD"},
             ), \
             patch.object(FR, "update_workflow_state", side_effect=fake_update):
            FR._advance_checkpoint_progress("slug", "diff", "NEWHEAD", repoint_only=repoint_only)
        return captured[FR.CHECKPOINT_PROGRESS_KEY]

    def test_repoint_only_keeps_index_but_repoints_head(self) -> None:
        progress = self._run(repoint_only=True, start_index=1)
        self.assertEqual(progress["index"], 1, "repair must not advance the slice index")
        self.assertEqual(progress["last_diff_head_sha"], "NEWHEAD", "stale diff should be re-pointed")

    def test_normal_advance_increments_index(self) -> None:
        progress = self._run(repoint_only=False, start_index=1)
        self.assertEqual(progress["index"], 2, "a completed slice should advance the index")
        self.assertEqual(progress["last_diff_head_sha"], "NEWHEAD")

    def test_non_diff_stage_is_a_noop(self) -> None:
        with patch.object(FR, "update_workflow_state", side_effect=AssertionError("must not write")):
            FR._advance_checkpoint_progress("slug", "spec", "NEWHEAD", repoint_only=False)

    def test_repair_checkpoint_args_sets_repair_flag(self) -> None:
        with patch.object(FR, "load_checkpoint_manifest", return_value={}), \
             patch.object(FR, "load_workflow_state", return_value={}):
            ns = FR.repair_checkpoint_args("slug", "spec", {"artifact_path": "/tmp/spec.md"})
        self.assertTrue(getattr(ns, "repair", False), "repair args must carry repair=True")


_TASKS_MD = """# Tasks

- [x] T1 implement app/games/foo/agents_lifecycle.py [P: app/games/foo/agents_lifecycle.py]
- [x] T2 add tests/test_agent_versions.py
- end of CP1 [CHECKPOINT]
- [ ] T8 implement app/web/routes.py
- end of CP2 [CHECKPOINT]
"""


class SliceDeclaredFilesAndGuardTests(unittest.TestCase):
    """Bug 2: slice file extraction + prior-slice-unbuilt drift guard."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        self.run = self.root / "run"
        self.run.mkdir(parents=True)
        self.repo.mkdir(parents=True)
        (self.run / "tasks.md").write_text(_TASKS_MD, encoding="utf-8")

    def _patches(self):
        return (
            patch.object(FP._stages, "workflow_dir", return_value=self.run),
            patch.object(FP._stages, "REPO_ROOT", self.repo),
        )

    def test_slice_declared_files_extracts_paths_per_slice(self) -> None:
        p1, p2 = self._patches()
        with p1, p2:
            slice0 = FP.slice_declared_files("slug", 0)
            slice1 = FP.slice_declared_files("slug", 1)
        self.assertEqual(
            slice0,
            ["app/games/foo/agents_lifecycle.py", "tests/test_agent_versions.py"],
        )
        self.assertEqual(slice1, ["app/web/routes.py"])

    def test_prior_slice_unbuilt_flags_missing_files(self) -> None:
        p1, p2 = self._patches()
        with p1, p2:
            # Building slice index 1 while slice 0's files don't exist → drift.
            msg = FP.prior_slice_unbuilt("slug", 1)
        self.assertIsNotNone(msg)
        self.assertIn("drifted", msg)

    def test_prior_slice_built_returns_none(self) -> None:
        # Create one of slice 0's declared files → prior slice looks built.
        target = self.repo / "app/games/foo/agents_lifecycle.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
        p1, p2 = self._patches()
        with p1, p2:
            self.assertIsNone(FP.prior_slice_unbuilt("slug", 1))

    def test_first_slice_has_no_prior_to_check(self) -> None:
        p1, p2 = self._patches()
        with p1, p2:
            self.assertIsNone(FP.prior_slice_unbuilt("slug", 0))

    def test_slice_with_no_declared_files_fails_open(self) -> None:
        (self.run / "tasks.md").write_text(
            "# Tasks\n\n- [x] T1 do a thing with no paths\n- end [CHECKPOINT]\n- [ ] T2 next\n",
            encoding="utf-8",
        )
        p1, p2 = self._patches()
        with p1, p2:
            self.assertIsNone(FP.prior_slice_unbuilt("slug", 1))


if __name__ == "__main__":
    unittest.main()
