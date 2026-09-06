import argparse
import contextlib
import gc
import io
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_state as FACTORY_STATE  # noqa: E402
import factory_cmd_checkpoint as FACTORY_CMD_CHECKPOINT  # noqa: E402
import factory_cmd_deliver as FACTORY_CMD_DELIVER  # noqa: E402


class SelfDocumentingErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_root = Path(self._tmpdir.name)
        self.runs_root = self.repo_root / "docs" / "workflow" / "feature-runs"
        self.slug = "self-doc"
        self.workflow_dir = self.runs_root / self.slug
        self.workflow_dir.mkdir(parents=True, exist_ok=True)

        # Patch every loaded factory_state module instance so that lazy imports
        # inside record_command_telemetry (and similar) also see the tmpdir.
        self._patches: list = []
        for mod in list(gc.get_objects()):
            if not isinstance(mod, types.ModuleType):
                continue
            if getattr(mod, "__name__", "") != "factory_state":
                continue
            if hasattr(mod, "FACTORY_RUNS_ROOT"):
                p = mock.patch.object(mod, "FACTORY_RUNS_ROOT", self.runs_root)
                p.start()
                self._patches.append(p)
            if hasattr(mod, "REPO_ROOT"):
                p = mock.patch.object(mod, "REPO_ROOT", self.repo_root)
                p.start()
                self._patches.append(p)
        self.addCleanup(lambda: [p.stop() for p in self._patches])

        self._cmd_checkpoint_repo_patch = mock.patch.object(FACTORY_CMD_CHECKPOINT, "REPO_ROOT", self.repo_root)
        self._cmd_deliver_repo_patch = mock.patch.object(FACTORY_CMD_DELIVER, "REPO_ROOT", self.repo_root)
        self._cmd_checkpoint_repo_patch.start()
        self._cmd_deliver_repo_patch.start()
        self.addCleanup(self._cmd_checkpoint_repo_patch.stop)
        self.addCleanup(self._cmd_deliver_repo_patch.stop)

    def _checkpoint_args(self, **overrides) -> argparse.Namespace:
        args = {
            "slug": self.slug,
            "stage": "diff",
            "artifact": None,
            "base_ref": None,
            "context": [],
            "path": [],
            "extra_gemini_lens": [],
            "sensitive": False,
            "large_structural": False,
            "performance_sensitive": False,
            "use_existing_artifact": False,
            "no_auto_context": False,
            "allow_dirty_path": [],
            "max_artifact_chars": 50000,
            "max_context_chars": 60000,
            "max_total_chars": 250000,
            "gemini_timeout_seconds": 120,
            "gemini_retries": 1,
            "repair_timeout_seconds": 300,
            "allow_large_diff_rerun": False,
            "fallback": False,
            "json": False,
            "keep_intermediates": False,
            "fast": False,
        }
        args.update(overrides)
        return argparse.Namespace(**args)

    def _deliver_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            slug=self.slug,
            create_pr=False,
            draft=False,
            base=None,
            title=None,
            reason=None,
            refresh=False,
            resume_merge_wait=False,
            watch_ci=False,
            interval=10,
            merge_when_green=False,
            auto_merge=False,
            dry_run=False,
            override_implementation_rule=False,
            override_implementation_reason=None,
        )

    def test_diff_cap_message_includes_allow_large_diff_rerun_flag(self) -> None:
        artifact = self.workflow_dir / "reviews" / "implementation.diff.patch"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("x", encoding="utf-8")

        with mock.patch.object(FACTORY_CMD_CHECKPOINT, "ensure_sync", return_value=None), \
            mock.patch.object(FACTORY_CMD_CHECKPOINT, "prerequisite_failure", return_value=None), \
            mock.patch.object(
                FACTORY_CMD_CHECKPOINT,
                "diff_review_budget_state",
                return_value={
                    "codex_review_present": True,
                    "large_artifact": True,
                    "artifact_exists": True,
                    "artifact_changed_since_codex": True,
                },
            ):
            with self.assertRaises(SystemExit) as ctx:
                FACTORY_CMD_CHECKPOINT.command_checkpoint(self._checkpoint_args(use_existing_artifact=True))

        self.assertIn("--allow-large-diff-rerun", str(ctx.exception))
        self.assertIn("checkpoint --slug self-doc --stage diff", str(ctx.exception))

    def test_dirty_path_message_includes_allow_dirty_path_flag(self) -> None:
        scope_manifest = self.workflow_dir / "scope.json"
        scope_manifest.parent.mkdir(parents=True, exist_ok=True)
        scope_manifest.write_text("{\"paths\": []}", encoding="utf-8")

        def fake_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and str(FACTORY_CMD_CHECKPOINT.WRITE_DIFF) in cmd:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="docs/workflow/feature-runs/self-doc/dirty.txt is dirty outside scope")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch.object(FACTORY_CMD_CHECKPOINT, "ensure_sync", return_value=None), \
            mock.patch.object(FACTORY_CMD_CHECKPOINT, "prerequisite_failure", return_value=None), \
            mock.patch.object(FACTORY_CMD_CHECKPOINT.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(SystemExit) as ctx:
                FACTORY_CMD_CHECKPOINT.command_checkpoint(self._checkpoint_args())

        self.assertIn("--allow-dirty-path <path>", str(ctx.exception))
        self.assertIn("checkpoint --slug self-doc --stage diff", str(ctx.exception))

    def test_head_mismatch_message_includes_validation_only_flag(self) -> None:
        message = (
            "deliver requires the current branch to match the reviewed diff HEAD; "
            "diff artifact HEAD 111111111111 does not match current HEAD 222222222222. "
            "Rerun reconcile first so the diff manifest can auto-reseal, or run "
            f"checkpoint --slug {self.slug} --stage diff --validation-only before delivering."
        )
        self.assertIn("checkpoint --slug self-doc --stage diff --validation-only", message)
        self.assertIn("diff artifact HEAD", message)


if __name__ == "__main__":
    unittest.main()
