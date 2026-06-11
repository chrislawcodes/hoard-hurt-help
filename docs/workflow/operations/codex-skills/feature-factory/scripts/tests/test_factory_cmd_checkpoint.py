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

REVIEW_LENS_SCRIPTS = Path(__file__).resolve().parents[3] / "review-lens" / "scripts"


def _load_or_reuse(name: str, path: Path):
    """Import an engine module by canonical name, reusing any already-loaded copy.

    These engine scripts are imported by bare module name across several test
    files. Re-exec'ing a fresh copy here and overwriting ``sys.modules[name]``
    would swap the module object out from under a sibling test that captured the
    earlier copy -- e.g. test_run_factory_repair grabs
    ``sys.modules['factory_cmd_checkpoint']`` at import time and then patches it.
    When repair is collected before this file, that clobber makes its patch land
    on a stale object while production code's lazy
    ``from factory_cmd_checkpoint import command_checkpoint`` reads the
    replacement -- an order-dependent failure. Reusing the existing singleton
    keeps exactly one object per module so every test patches what runs.
    """
    existing = sys.modules.get(name)
    if (
        existing is not None
        and getattr(existing, "__file__", None) is not None
        and Path(existing.__file__).resolve() == Path(path).resolve()
    ):
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FACTORY_STATE = _load_or_reuse("factory_state", SCRIPT_DIR / "factory_state.py")
CHECKPOINT = _load_or_reuse("factory_cmd_checkpoint", SCRIPT_DIR / "factory_cmd_checkpoint.py")
WORKFLOW_UTILS = _load_or_reuse("workflow_utils", REVIEW_LENS_SCRIPTS / "workflow_utils.py")


def _base_state() -> dict:
    state = FACTORY_STATE._default_workflow_state()
    state["schema_version"] = 2
    return state


def _spec_stage_state(adversarial_rounds: int) -> dict:
    return {
        "adversarial_rounds": adversarial_rounds,
        "annotations": [],
        "adversarial_sha_history": [],
        "initial_sha": "",
    }


def _write_workflow_state(tmpdir: str, stage_state: dict) -> Path:
    with patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", Path(tmpdir)):
        workflow_root = FACTORY_STATE.workflow_dir("ff-checkpoint-test")
        workflow_root.mkdir(parents=True, exist_ok=True)
        artifact_path = workflow_root / "spec.md"
        artifact_path.write_text("# Spec\n\nMeaningful content for checkpoint tests.\n", encoding="utf-8")
        state = _base_state()
        state["stages"] = {"spec": stage_state}
        state["spec_adversarial_rounds"] = stage_state["adversarial_rounds"]
        FACTORY_STATE.atomic_json_write(FACTORY_STATE.factory_state_path("ff-checkpoint-test"), state)
        return artifact_path


def _args(json_flag: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        slug="ff-checkpoint-test",
        stage="spec",
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
        json=json_flag,
        fast=False,
    )


def _fake_stage_manifest_state(slug: str, stage: str) -> dict[str, object]:
    if stage == "spec":
        return {
            "artifact_path": Path("spec.md"),
            "artifact_exists": True,
            "artifact_meaningful": True,
            "manifest_path": Path("spec.checkpoint.json"),
            "manifest_exists": True,
            "healthy": True,
            "detail": "",
        }
    return {
        "artifact_path": Path(f"{stage}.md"),
        "artifact_exists": False,
        "artifact_meaningful": False,
        "manifest_path": Path(f"{stage}.checkpoint.json"),
        "manifest_exists": False,
        "healthy": False,
        "detail": "",
    }


class FactoryCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        # Patch every loaded factory_state module instance so that lazy imports
        # inside record_command_telemetry (and similar) also see the tmpdir.
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

    def _prepare_state(self, adversarial_rounds: int) -> Path:
        return _write_workflow_state(self._tmpdir.name, _spec_stage_state(adversarial_rounds))

    def test_effective_auto_context_defaults_to_spec_and_tasks_only(self) -> None:
        def enabled(stage: str) -> bool:
            return CHECKPOINT._effective_auto_context(argparse.Namespace(
                stage=stage,
                auto_context=False,
                no_auto_context=False,
            ))

        self.assertTrue(enabled("spec"))
        self.assertTrue(enabled("tasks"))
        self.assertFalse(enabled("plan"))
        self.assertFalse(enabled("diff"))

    def test_effective_auto_context_flags_override_stage_defaults(self) -> None:
        self.assertTrue(CHECKPOINT._effective_auto_context(argparse.Namespace(
            stage="diff",
            auto_context=True,
            no_auto_context=False,
        )))
        self.assertFalse(CHECKPOINT._effective_auto_context(argparse.Namespace(
            stage="spec",
            auto_context=False,
            no_auto_context=True,
        )))

    def test_checkpoint_increments_adversarial_rounds_on_success(self) -> None:
        self._prepare_state(2)
        args = _args()
        fake_result = subprocess.CompletedProcess(args=["repair"], returncode=0, stdout="", stderr="")
        with patch.object(CHECKPOINT, "ensure_sync", return_value=None), \
            patch.object(CHECKPOINT, "revert_protected_files", return_value=[]), \
            patch.object(CHECKPOINT, "stage_manifest_state", side_effect=_fake_stage_manifest_state), \
            patch.object(CHECKPOINT, "reconciliation_state", return_value=(True, "")), \
            patch.object(CHECKPOINT, "_emit_next_action", return_value=None), \
            patch.object(CHECKPOINT.subprocess, "run", return_value=fake_result):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = CHECKPOINT.command_checkpoint(args)

        self.assertEqual(rc, 0)
        persisted = json.loads(FACTORY_STATE.factory_state_path("ff-checkpoint-test").read_text(encoding="utf-8"))
        spec_state = persisted["stages"]["spec"]
        expected_sha = WORKFLOW_UTILS.normalized_artifact_hash("spec", FACTORY_STATE.workflow_dir("ff-checkpoint-test") / "spec.md")
        self.assertEqual(spec_state["adversarial_rounds"], 3)
        self.assertEqual(persisted["spec_adversarial_rounds"], 3)
        self.assertEqual(spec_state["initial_sha"], expected_sha)
        self.assertEqual(spec_state["adversarial_sha_history"], [expected_sha])

    def test_checkpoint_decrements_on_dispatch_failure(self) -> None:
        self._prepare_state(2)
        args = _args()
        with patch.object(CHECKPOINT, "ensure_sync", return_value=None), \
            patch.object(CHECKPOINT, "revert_protected_files", return_value=[]), \
            patch.object(CHECKPOINT, "stage_manifest_state", side_effect=_fake_stage_manifest_state), \
            patch.object(CHECKPOINT, "reconciliation_state", return_value=(True, "")), \
            patch.object(CHECKPOINT, "_emit_next_action", return_value=None), \
            patch.object(CHECKPOINT.subprocess, "run", side_effect=RuntimeError("dispatch failed")):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    CHECKPOINT.command_checkpoint(args)

        persisted = json.loads(FACTORY_STATE.factory_state_path("ff-checkpoint-test").read_text(encoding="utf-8"))
        spec_state = persisted["stages"]["spec"]
        self.assertEqual(spec_state["adversarial_rounds"], 2)
        self.assertEqual(persisted["spec_adversarial_rounds"], 2)

    def test_checkpoint_records_initial_sha_on_first_call_and_appends_to_adversarial_sha_history_thereafter(self) -> None:
        self._prepare_state(0)
        args = _args()
        first_result = subprocess.CompletedProcess(args=["repair"], returncode=0, stdout="", stderr="")
        with patch.object(CHECKPOINT, "ensure_sync", return_value=None), \
            patch.object(CHECKPOINT, "revert_protected_files", return_value=[]), \
            patch.object(CHECKPOINT, "stage_manifest_state", side_effect=_fake_stage_manifest_state), \
            patch.object(CHECKPOINT, "reconciliation_state", return_value=(True, "")), \
            patch.object(CHECKPOINT, "_emit_next_action", return_value=None), \
            patch.object(CHECKPOINT.subprocess, "run", return_value=first_result):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = CHECKPOINT.command_checkpoint(args)

        self.assertEqual(rc, 0)
        first_sha = WORKFLOW_UTILS.normalized_artifact_hash("spec", FACTORY_STATE.workflow_dir("ff-checkpoint-test") / "spec.md")
        persisted = json.loads(FACTORY_STATE.factory_state_path("ff-checkpoint-test").read_text(encoding="utf-8"))
        self.assertEqual(persisted["stages"]["spec"]["initial_sha"], first_sha)
        self.assertEqual(persisted["stages"]["spec"]["adversarial_sha_history"], [first_sha])

        artifact_path = FACTORY_STATE.workflow_dir("ff-checkpoint-test") / "spec.md"
        artifact_path.write_text("# Spec\n\nMeaningful content for checkpoint tests, revised.\n", encoding="utf-8")
        second_result = subprocess.CompletedProcess(args=["repair"], returncode=0, stdout="", stderr="")
        with patch.object(CHECKPOINT, "ensure_sync", return_value=None), \
            patch.object(CHECKPOINT, "revert_protected_files", return_value=[]), \
            patch.object(CHECKPOINT, "stage_manifest_state", side_effect=_fake_stage_manifest_state), \
            patch.object(CHECKPOINT, "reconciliation_state", return_value=(True, "")), \
            patch.object(CHECKPOINT, "_emit_next_action", return_value=None), \
            patch.object(CHECKPOINT.subprocess, "run", return_value=second_result):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = CHECKPOINT.command_checkpoint(args)

        self.assertEqual(rc, 0)
        second_sha = WORKFLOW_UTILS.normalized_artifact_hash("spec", artifact_path)
        persisted = json.loads(FACTORY_STATE.factory_state_path("ff-checkpoint-test").read_text(encoding="utf-8"))
        self.assertEqual(persisted["stages"]["spec"]["initial_sha"], first_sha)
        self.assertEqual(persisted["stages"]["spec"]["adversarial_sha_history"], [first_sha, second_sha])


class FindingsSummaryTests(unittest.TestCase):
    """Tests for _print_findings_summary and its integration via command_checkpoint."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
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

    def _prepare_state(self, adversarial_rounds: int = 0) -> Path:
        return _write_workflow_state(self._tmpdir.name, _spec_stage_state(adversarial_rounds))

    def _reviews_dir(self, slug: str = "ff-checkpoint-test") -> Path:
        with patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", Path(self._tmpdir.name)):
            return FACTORY_STATE.reviews_dir(slug)

    def _capture_checkpoint_stderr(self, reviews_arg: list | None = None) -> str:
        """Run command_checkpoint with patched reviews and return stderr text."""
        self._prepare_state(0)
        args = _args()
        fake_result = subprocess.CompletedProcess(args=["repair"], returncode=0, stdout="", stderr="")
        stderr_buf = io.StringIO()
        with patch.object(CHECKPOINT, "ensure_sync", return_value=None), \
            patch.object(CHECKPOINT, "revert_protected_files", return_value=[]), \
            patch.object(CHECKPOINT, "stage_manifest_state", side_effect=_fake_stage_manifest_state), \
            patch.object(CHECKPOINT, "reconciliation_state", return_value=(True, "")), \
            patch.object(CHECKPOINT, "_emit_next_action", return_value=None), \
            patch.object(CHECKPOINT, "required_reviews", return_value=reviews_arg or []), \
            patch.object(CHECKPOINT.subprocess, "run", return_value=fake_result):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr_buf):
                CHECKPOINT.command_checkpoint(args)
        return stderr_buf.getvalue()

    # ------------------------------------------------------------------
    # Unit tests for _print_findings_summary directly
    # ------------------------------------------------------------------

    def test_no_reviews_configured_prints_no_default_reviews_message(self) -> None:
        self._prepare_state(0)
        reviews_dir = self._reviews_dir()
        reviews_dir.mkdir(parents=True, exist_ok=True)
        stderr_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf):
            CHECKPOINT._print_findings_summary("ff-checkpoint-test", "tasks", [])
        output = stderr_buf.getvalue()
        self.assertIn("[ff] checkpoint completed for stage=tasks", output)
        self.assertIn("no default reviews configured", output)

    def test_reviews_ran_but_no_findings_prints_clean_message(self) -> None:
        self._prepare_state(0)
        reviews_dir = self._reviews_dir()
        reviews_dir.mkdir(parents=True, exist_ok=True)
        # Write a clean review with no findings
        (reviews_dir / "spec.codex.feasibility-adversarial.review.md").write_text(
            "## Findings\n\nNo issues found. Everything looks good.\n",
            encoding="utf-8",
        )
        fake_reviews = [{"reviewer": "codex", "lens": "feasibility-adversarial"}]
        stderr_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf):
            CHECKPOINT._print_findings_summary("ff-checkpoint-test", "spec", fake_reviews)
        output = stderr_buf.getvalue()
        self.assertIn("reviews ran, no actionable findings raised", output)
        self.assertNotIn("findings raised:", output)

    def test_reviews_with_findings_prints_per_file_severity_counts(self) -> None:
        self._prepare_state(0)
        reviews_dir = self._reviews_dir()
        reviews_dir.mkdir(parents=True, exist_ok=True)
        (reviews_dir / "plan.codex.implementation-adversarial.review.md").write_text(
            "## Findings\n\n- medium: bootstrap CI missing seed\n",
            encoding="utf-8",
        )
        (reviews_dir / "plan.gemini.testability-adversarial.review.md").write_text(
            "## Findings\n\n- high: maxOrderEffect placed at row level\n",
            encoding="utf-8",
        )
        fake_reviews = [
            {"reviewer": "codex", "lens": "implementation-adversarial"},
            {"reviewer": "gemini", "lens": "testability-adversarial"},
        ]
        stderr_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf):
            CHECKPOINT._print_findings_summary("ff-checkpoint-test", "plan", fake_reviews)
        output = stderr_buf.getvalue()
        self.assertIn("[ff] findings raised:", output)
        self.assertIn("plan.codex.implementation-adversarial.review.md: 1 MEDIUM", output)
        self.assertIn("plan.gemini.testability-adversarial.review.md: 1 HIGH", output)
        self.assertIn("[ff] inspect:", output)
        self.assertIn("docs/workflow/feature-runs/ff-checkpoint-test/reviews/plan.codex.implementation-adversarial.review.md", output)
        self.assertIn("docs/workflow/feature-runs/ff-checkpoint-test/reviews/plan.gemini.testability-adversarial.review.md", output)

    def test_only_files_with_findings_appear_in_summary(self) -> None:
        self._prepare_state(0)
        reviews_dir = self._reviews_dir()
        reviews_dir.mkdir(parents=True, exist_ok=True)
        (reviews_dir / "spec.codex.feasibility-adversarial.review.md").write_text(
            "## Findings\n\n- high: serious problem\n",
            encoding="utf-8",
        )
        (reviews_dir / "spec.gemini.requirements-adversarial.review.md").write_text(
            "## Findings\n\nAll good. No issues.\n",
            encoding="utf-8",
        )
        fake_reviews = [
            {"reviewer": "codex", "lens": "feasibility-adversarial"},
            {"reviewer": "gemini", "lens": "requirements-adversarial"},
        ]
        stderr_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf):
            CHECKPOINT._print_findings_summary("ff-checkpoint-test", "spec", fake_reviews)
        output = stderr_buf.getvalue()
        self.assertIn("spec.codex.feasibility-adversarial.review.md", output)
        self.assertNotIn("spec.gemini.requirements-adversarial.review.md", output)

    def test_stage_with_no_default_reviews_prints_no_default_message(self) -> None:
        # tasks/diff/closeout get empty reviews_arg by default
        self._prepare_state(0)
        reviews_dir = self._reviews_dir()
        reviews_dir.mkdir(parents=True, exist_ok=True)
        stderr_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf):
            CHECKPOINT._print_findings_summary("ff-checkpoint-test", "tasks", [])
        output = stderr_buf.getvalue()
        self.assertIn("[ff] checkpoint completed for stage=tasks", output)
        self.assertIn("no default reviews configured for this stage", output)

    def test_idempotent_rerun_produces_same_summary(self) -> None:
        self._prepare_state(0)
        reviews_dir = self._reviews_dir()
        reviews_dir.mkdir(parents=True, exist_ok=True)
        (reviews_dir / "plan.codex.implementation-adversarial.review.md").write_text(
            "## Findings\n\n- high: found an issue\n",
            encoding="utf-8",
        )
        fake_reviews = [{"reviewer": "codex", "lens": "implementation-adversarial"}]
        buf1 = io.StringIO()
        buf2 = io.StringIO()
        with contextlib.redirect_stderr(buf1):
            CHECKPOINT._print_findings_summary("ff-checkpoint-test", "plan", fake_reviews)
        with contextlib.redirect_stderr(buf2):
            CHECKPOINT._print_findings_summary("ff-checkpoint-test", "plan", fake_reviews)
        self.assertEqual(buf1.getvalue(), buf2.getvalue())

    # ------------------------------------------------------------------
    # Integration test: findings summary appears in command_checkpoint output
    # ------------------------------------------------------------------

    def test_checkpoint_success_emits_findings_summary_to_stderr(self) -> None:
        self._prepare_state(0)
        reviews_dir = self._reviews_dir()
        reviews_dir.mkdir(parents=True, exist_ok=True)
        (reviews_dir / "spec.codex.feasibility-adversarial.review.md").write_text(
            "## Findings\n\n- high: missing validation\n",
            encoding="utf-8",
        )
        args = _args()
        fake_result = subprocess.CompletedProcess(args=["repair"], returncode=0, stdout="", stderr="")
        fake_reviews = [{"reviewer": "codex", "lens": "feasibility-adversarial", "model": "gpt-5.4-mini"}]
        stderr_buf = io.StringIO()
        with patch.object(CHECKPOINT, "ensure_sync", return_value=None), \
            patch.object(CHECKPOINT, "revert_protected_files", return_value=[]), \
            patch.object(CHECKPOINT, "stage_manifest_state", side_effect=_fake_stage_manifest_state), \
            patch.object(CHECKPOINT, "reconciliation_state", return_value=(True, "")), \
            patch.object(CHECKPOINT, "_emit_next_action", return_value=None), \
            patch.object(CHECKPOINT, "required_reviews", return_value=fake_reviews), \
            patch.object(CHECKPOINT.subprocess, "run", return_value=fake_result):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr_buf):
                rc = CHECKPOINT.command_checkpoint(args)
        self.assertEqual(rc, 0)
        output = stderr_buf.getvalue()
        self.assertIn("[ff] checkpoint completed for stage=spec", output)
        self.assertIn("1 HIGH", output)


if __name__ == "__main__":
    unittest.main()
