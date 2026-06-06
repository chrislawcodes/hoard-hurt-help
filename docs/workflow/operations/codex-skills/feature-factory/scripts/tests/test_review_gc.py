import argparse
import contextlib
import gc
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1]

STATE_SPEC = importlib.util.spec_from_file_location("factory_state", SCRIPT_DIR / "factory_state.py")
assert STATE_SPEC and STATE_SPEC.loader
FACTORY_STATE = importlib.util.module_from_spec(STATE_SPEC)
sys.modules[STATE_SPEC.name] = FACTORY_STATE
STATE_SPEC.loader.exec_module(FACTORY_STATE)

CHECKPOINT_SPEC = importlib.util.spec_from_file_location("factory_cmd_checkpoint", SCRIPT_DIR / "factory_cmd_checkpoint.py")
assert CHECKPOINT_SPEC and CHECKPOINT_SPEC.loader
CHECKPOINT = importlib.util.module_from_spec(CHECKPOINT_SPEC)
sys.modules[CHECKPOINT_SPEC.name] = CHECKPOINT
CHECKPOINT_SPEC.loader.exec_module(CHECKPOINT)


SLUG = "ff-gc-checkpoint"


def _base_state() -> dict:
    state = FACTORY_STATE._default_workflow_state()
    state["schema_version"] = 2
    return state


def _stage_state(adversarial_rounds: int = 2) -> dict:
    return {
        "adversarial_rounds": adversarial_rounds,
        "judge_rounds": 0,
        "judge_verdicts": [],
        "annotations": [],
        "unresolved_concerns": [],
        "adversarial_sha_history": [],
        "initial_sha": "",
    }


def _checkpoint_args(*, keep_intermediates: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        slug=SLUG,
        stage="spec",
        address=None,
        defer=None,
        dismiss=None,
        evidence=None,
        reason=None,
        artifact=None,
        base_ref=None,
        context=[],
        path=[],
        extra_gemini_lens=[],
        sensitive=False,
        large_structural=False,
        performance_sensitive=False,
        use_existing_artifact=False,
        no_auto_context=True,
        allow_dirty_path=[],
        max_artifact_chars=50000,
        max_context_chars=100000,
        max_total_chars=180000,
        gemini_timeout_seconds=120,
        gemini_retries=1,
        repair_timeout_seconds=300,
        allow_large_diff_rerun=False,
        fallback=False,
        json=False,
        fast=False,
        keep_intermediates=keep_intermediates,
    )


def _write_workflow(tmpdir: str, stage_state: dict, extra_files: dict[str, str] | None = None) -> Path:
    with patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", Path(tmpdir)):
        root = FACTORY_STATE.workflow_dir(SLUG)
        reviews = FACTORY_STATE.reviews_dir(SLUG)
        root.mkdir(parents=True, exist_ok=True)
        reviews.mkdir(parents=True, exist_ok=True)
        (root / "spec.md").write_text("# Spec\n\nMeaningful content for checkpoint GC tests.\n", encoding="utf-8")
        state = _base_state()
        state["stages"] = {"spec": stage_state}
        state["spec_adversarial_rounds"] = stage_state["adversarial_rounds"]
        state["spec_judge_rounds"] = stage_state["judge_rounds"]
        FACTORY_STATE.atomic_json_write(FACTORY_STATE.factory_state_path(SLUG), state)
        for relpath, contents in (extra_files or {}).items():
            path = reviews / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")
        return root


def _make_stage_manifest_state(slug: str, stage: str) -> dict[str, object]:
    return {
        "artifact_path": FACTORY_STATE.workflow_dir(slug) / f"{stage}.md",
        "artifact_exists": True,
        "artifact_meaningful": True,
        "manifest_path": FACTORY_STATE.reviews_dir(slug) / f"{stage}.checkpoint.json",
        "manifest_exists": False,
        "healthy": False,
        "detail": "",
    }


class ReviewGcTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        # Patch every loaded factory_state module instance so that lazy imports
        # inside record_command_telemetry (and similar) also see the tmpdir.
        # Multiple test files each load factory_state via importlib into separate
        # module objects; patching only FACTORY_STATE misses instances referenced
        # by other already-loaded modules.
        self._patches: list = []
        for mod in list(gc.get_objects()):
            if not isinstance(mod, types.ModuleType):
                continue
            if getattr(mod, "__name__", "") != "factory_state":
                continue
            if not hasattr(mod, "FACTORY_RUNS_ROOT"):
                continue
            p = patch.object(mod, "FACTORY_RUNS_ROOT", Path(self._tmpdir.name))
            p.start()
            self._patches.append(p)
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def _run_checkpoint(self, args: argparse.Namespace) -> tuple[int, str, str]:
        fake_result = subprocess.CompletedProcess(args=["repair"], returncode=0, stdout="", stderr="")
        with patch.object(CHECKPOINT, "ensure_sync", return_value=None), \
            patch.object(CHECKPOINT, "prerequisite_failure", return_value=None), \
            patch.object(CHECKPOINT, "revert_protected_files", return_value=[]), \
            patch.object(CHECKPOINT, "stage_manifest_state", side_effect=_make_stage_manifest_state), \
            patch.object(CHECKPOINT, "reconciliation_state", return_value=(True, "")), \
            patch.object(CHECKPOINT, "_emit_next_action", return_value=None), \
            patch.object(CHECKPOINT.subprocess, "run", return_value=fake_result):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = CHECKPOINT.command_checkpoint(args)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_gc_deletes_target_stage_intermediates_and_preserves_authoritative_files(self) -> None:
        reviews = {
            "spec.codex.feasibility-adversarial.review.md.narrowed.txt": "narrowed text",
            "spec.codex.feasibility-adversarial.review.md.narrowed.json": "{}",
            "spec.codex.feasibility-adversarial.review.md.raw.txt": "raw text",
            "spec.codex.feasibility-adversarial.review.md.stdout.txt": "stdout",
            "spec.codex.feasibility-adversarial.review.md.stderr.txt": "stderr",
            "spec.codex.feasibility-adversarial.review.md": "# review",
            "spec.checkpoint.json": "{}",
        }
        _write_workflow(self._tmpdir.name, _stage_state(), reviews)

        rc, _, _ = self._run_checkpoint(_checkpoint_args())
        self.assertEqual(rc, 0)

        reviews_dir = FACTORY_STATE.reviews_dir(SLUG)
        for relpath in (
            "spec.codex.feasibility-adversarial.review.md.narrowed.txt",
            "spec.codex.feasibility-adversarial.review.md.narrowed.json",
            "spec.codex.feasibility-adversarial.review.md.raw.txt",
            "spec.codex.feasibility-adversarial.review.md.stdout.txt",
            "spec.codex.feasibility-adversarial.review.md.stderr.txt",
        ):
            self.assertFalse((reviews_dir / relpath).exists(), relpath)
        self.assertTrue((reviews_dir / "spec.codex.feasibility-adversarial.review.md").exists())
        self.assertTrue((reviews_dir / "spec.checkpoint.json").exists())

    def test_keep_intermediates_suppresses_deletion(self) -> None:
        reviews = {
            "spec.codex.feasibility-adversarial.review.md.narrowed.txt": "narrowed text",
            "spec.codex.feasibility-adversarial.review.md.raw.txt": "raw text",
            "spec.codex.feasibility-adversarial.review.md.stdout.txt": "stdout",
            "spec.codex.feasibility-adversarial.review.md.stderr.txt": "stderr",
            "spec.codex.feasibility-adversarial.review.md.narrowed.json": "{}",
        }
        _write_workflow(self._tmpdir.name, _stage_state(), reviews)

        rc, _, _ = self._run_checkpoint(_checkpoint_args(keep_intermediates=True))
        self.assertEqual(rc, 0)

        reviews_dir = FACTORY_STATE.reviews_dir(SLUG)
        for relpath in reviews:
            self.assertTrue((reviews_dir / relpath).exists(), relpath)

    def test_gc_is_scoped_to_target_stage(self) -> None:
        reviews = {
            "spec.codex.feasibility-adversarial.review.md.raw.txt": "spec raw",
            "diff.codex.feasibility-adversarial.review.md.raw.txt": "diff raw",
            "diff.codex.feasibility-adversarial.review.md.stdout.txt": "diff stdout",
        }
        _write_workflow(self._tmpdir.name, _stage_state(), reviews)

        rc, _, _ = self._run_checkpoint(_checkpoint_args())
        self.assertEqual(rc, 0)

        reviews_dir = FACTORY_STATE.reviews_dir(SLUG)
        self.assertFalse((reviews_dir / "spec.codex.feasibility-adversarial.review.md.raw.txt").exists())
        self.assertTrue((reviews_dir / "diff.codex.feasibility-adversarial.review.md.raw.txt").exists())
        self.assertTrue((reviews_dir / "diff.codex.feasibility-adversarial.review.md.stdout.txt").exists())

    def test_gc_runs_inside_state_lock(self) -> None:
        _write_workflow(self._tmpdir.name, _stage_state(), {})
        events: list[str] = []
        original_with_locked_state = CHECKPOINT.with_locked_state

        @contextlib.contextmanager
        def traced_with_locked_state(slug: str):
            events.append("enter")
            with original_with_locked_state(slug) as state:
                events.append("entered")
                yield state
            events.append("exit")

        def gc_side_effect(slug: str, stage: str, keep: bool) -> list[Path]:
            events.append("gc")
            return []

        fake_result = subprocess.CompletedProcess(args=["repair"], returncode=0, stdout="", stderr="")
        with patch.object(CHECKPOINT, "ensure_sync", return_value=None), \
            patch.object(CHECKPOINT, "prerequisite_failure", return_value=None), \
            patch.object(CHECKPOINT, "revert_protected_files", return_value=[]), \
            patch.object(CHECKPOINT, "stage_manifest_state", side_effect=_make_stage_manifest_state), \
            patch.object(CHECKPOINT, "reconciliation_state", return_value=(True, "")), \
            patch.object(CHECKPOINT, "_emit_next_action", return_value=None), \
            patch.object(CHECKPOINT, "with_locked_state", side_effect=traced_with_locked_state), \
            patch.object(CHECKPOINT, "_gc_review_intermediates", side_effect=gc_side_effect), \
            patch.object(CHECKPOINT.subprocess, "run", return_value=fake_result):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = CHECKPOINT.command_checkpoint(_checkpoint_args())

        self.assertEqual(rc, 0)
        self.assertGreaterEqual(events.count("enter"), 2)
        self.assertEqual(events[0:3], ["enter", "entered", "gc"])


if __name__ == "__main__":
    unittest.main()
