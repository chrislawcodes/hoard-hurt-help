"""Tests for the hard architecture-awareness gates:
- reuse-report.md required before the plan checkpoint (prerequisite_failure)
- ARCHITECTURE.md/DESIGN.md resolved before `done` (recommended_next_action)
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FACTORY_STATE = _load("factory_state")
FACTORY_STAGES = _load("factory_stages")
NEXT_ACTION = _load("factory_next_action")


def _healthy_stages() -> dict:
    keys = ["spec", "plan", "tasks", "diff", "closeout"]
    return {
        k: {"artifact_exists": True, "artifact_meaningful": True, "manifest_exists": True, "healthy": True}
        for k in keys
    }


class ReuseReportMeaningfulTests(unittest.TestCase):
    def test_missing_is_not_meaningful(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(FACTORY_STAGES, "workflow_dir", return_value=Path(d)):
                self.assertFalse(FACTORY_STAGES.reuse_report_meaningful("slug"))

    def test_stub_heading_only_is_not_meaningful(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "reuse-report.md").write_text("# Reuse audit\n", encoding="utf-8")
            with mock.patch.object(FACTORY_STAGES, "workflow_dir", return_value=Path(d)):
                self.assertFalse(FACTORY_STAGES.reuse_report_meaningful("slug"))

    def test_real_content_is_meaningful(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "reuse-report.md").write_text(
                "# Reuse audit\n| cap | module | verdict |\n| chat | app/chat.py | reuse |\n",
                encoding="utf-8",
            )
            with mock.patch.object(FACTORY_STAGES, "workflow_dir", return_value=Path(d)):
                self.assertTrue(FACTORY_STAGES.reuse_report_meaningful("slug"))


class PlanPrerequisiteReuseGateTests(unittest.TestCase):
    def _patch_spec_ready(self):
        # Make the spec prerequisite pass so we reach the reuse check.
        return mock.patch.object(
            FACTORY_STAGES, "stage_manifest_state",
            return_value={"manifest_exists": True, "healthy": True},
        )

    def test_plan_blocked_when_init_present_and_no_reuse_report(self) -> None:
        with self._patch_spec_ready(), \
             mock.patch.object(FACTORY_STAGES, "load_workflow_state", return_value={"init_head_sha": "abc123"}), \
             mock.patch.object(FACTORY_STAGES, "reuse_report_meaningful", return_value=False):
            reason = FACTORY_STAGES.prerequisite_failure("slug", "plan")
        self.assertIsNotNone(reason)
        self.assertIn("reuse audit", reason)

    def test_plan_allowed_when_reuse_report_present(self) -> None:
        with self._patch_spec_ready(), \
             mock.patch.object(FACTORY_STAGES, "load_workflow_state", return_value={"init_head_sha": "abc123"}), \
             mock.patch.object(FACTORY_STAGES, "reuse_report_meaningful", return_value=True):
            self.assertIsNone(FACTORY_STAGES.prerequisite_failure("slug", "plan"))

    def test_plan_fails_open_without_init_sha(self) -> None:
        # Test fixtures / pre-gate runs have no init SHA → reuse gate does not fire.
        with self._patch_spec_ready(), \
             mock.patch.object(FACTORY_STAGES, "load_workflow_state", return_value={}), \
             mock.patch.object(FACTORY_STAGES, "reuse_report_meaningful", return_value=False):
            self.assertIsNone(FACTORY_STAGES.prerequisite_failure("slug", "plan"))


class ArchDocsResolvedTests(unittest.TestCase):
    def test_fails_open_without_init_sha(self) -> None:
        with mock.patch.object(FACTORY_STAGES, "load_workflow_state", return_value={}):
            self.assertTrue(FACTORY_STAGES.arch_docs_resolved("slug"))

    def test_resolved_when_acked(self) -> None:
        state = {"init_head_sha": "abc", "arch_docs": {"no_change_acked": True}}
        with mock.patch.object(FACTORY_STAGES, "load_workflow_state", return_value=state):
            self.assertTrue(FACTORY_STAGES.arch_docs_resolved("slug"))

    def test_resolved_when_docs_changed(self) -> None:
        state = {"init_head_sha": "abc"}
        with mock.patch.object(FACTORY_STAGES, "load_workflow_state", return_value=state), \
             mock.patch.object(FACTORY_STAGES.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
            self.assertTrue(FACTORY_STAGES.arch_docs_resolved("slug"))

    def test_not_resolved_when_unchanged_and_not_acked(self) -> None:
        state = {"init_head_sha": "abc"}
        with mock.patch.object(FACTORY_STAGES, "load_workflow_state", return_value=state), \
             mock.patch.object(FACTORY_STAGES.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            self.assertFalse(FACTORY_STAGES.arch_docs_resolved("slug"))


class NextActionArchGateTests(unittest.TestCase):
    def _drive_to_end(self, arch_resolved: bool) -> str:
        state = {
            "parallel_analysis": {"reviewed": True},
            "delivery": {"pr_url": "x", "checks_summary": "pass", "head_mismatch": False, "merge_state_status": "clean"},
        }
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "tasks.md").write_text("- [x] done", encoding="utf-8")
            (Path(d) / "postmortem.md").write_text("post mortem", encoding="utf-8")
            with mock.patch.object(NEXT_ACTION, "workflow_dir", return_value=Path(d)), \
                 mock.patch.object(NEXT_ACTION, "status_md_changed_since_init", return_value=True), \
                 mock.patch.object(NEXT_ACTION, "arch_docs_resolved", return_value=arch_resolved), \
                 mock.patch("factory_deliver.refresh_delivery_snapshot", return_value=state["delivery"]):
                return NEXT_ACTION.recommended_next_action("slug", state, _healthy_stages(), True)

    def test_blocks_done_when_arch_docs_unresolved(self) -> None:
        self.assertEqual(self._drive_to_end(arch_resolved=False), "reconcile_arch_docs")

    def test_done_when_arch_docs_resolved(self) -> None:
        self.assertEqual(self._drive_to_end(arch_resolved=True), "done")


class ScopedDocPathsTests(unittest.TestCase):
    """scoped_doc_paths maps a feature's scope.json to the relevant docs.

    Relies on the real repo docs (docs/platform/, docs/games/hoard-hurt-help/)
    existing under REPO_ROOT, which they do after the platform/game split.
    """
    def _paths(self, scope_paths):
        with mock.patch.object(FACTORY_STAGES, "load_scope_manifest",
                               return_value={"paths": scope_paths}):
            return FACTORY_STAGES.scoped_doc_paths("slug")

    def test_platform_scope_returns_platform_docs_only(self):
        paths = self._paths(["app/engine", "app/routes"])
        self.assertTrue(paths, "expected platform docs")
        self.assertTrue(all(p.startswith("docs/platform/") for p in paths), paths)

    def test_game_scope_returns_that_games_docs_only(self):
        paths = self._paths(["app/games/hoard_hurt_help"])
        self.assertTrue(paths, "expected game docs")
        self.assertTrue(all(p.startswith("docs/games/hoard-hurt-help/") for p in paths), paths)
        self.assertFalse(any(p.startswith("docs/platform/") for p in paths), paths)

    def test_mixed_scope_returns_both(self):
        paths = self._paths(["app/engine", "app/games/hoard_hurt_help"])
        self.assertTrue(any(p.startswith("docs/platform/") for p in paths), paths)
        self.assertTrue(any(p.startswith("docs/games/hoard-hurt-help/") for p in paths), paths)

    def test_empty_scope_defaults_to_platform(self):
        paths = self._paths([])
        self.assertTrue(any(p.startswith("docs/platform/") for p in paths), paths)


if __name__ == "__main__":
    unittest.main()
