import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_factory.py"
SPEC = importlib.util.spec_from_file_location("run_factory", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

# Use the modules that run_factory actually imported (same object identity)
# so that unittest.mock.patch.object targets the right namespace.
FACTORY_STATE = sys.modules["factory_state"]
STAGES_MODULE = sys.modules["factory_stages"]
REVIEW_MODULE = sys.modules["factory_review"]
FACTORY_GIT = sys.modules["factory_git"]
CMD_CHECKPOINT_MODULE = sys.modules["factory_cmd_checkpoint"]
CMD_STATUS_MODULE = sys.modules["factory_cmd_status"]
CMD_DELIVER_MODULE = sys.modules["factory_cmd_deliver"]
CMD_IMPLEMENT_MODULE = sys.modules["factory_cmd_implement"]
NEXT_ACTION_MODULE = sys.modules["factory_next_action"]
PARALLEL_MODULE = sys.modules["factory_parallel"]

REVIEW_LENS_SCRIPTS = Path(__file__).resolve().parents[2] / "review-lens" / "scripts"
if str(REVIEW_LENS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(REVIEW_LENS_SCRIPTS))


def load_review_lens_module(name: str):
    path = REVIEW_LENS_SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


REPAIR_MODULE = load_review_lens_module("repair_review_checkpoint")
ATTEMPTS_MODULE = load_review_lens_module("review_attempts")


def stage_state(
    *,
    artifact_exists: bool = False,
    artifact_meaningful: bool = False,
    manifest_exists: bool = False,
    healthy: bool = False,
    detail: str = "",
) -> dict[str, object]:
    return {
        "artifact_path": Path("/tmp/placeholder"),
        "artifact_exists": artifact_exists,
        "artifact_meaningful": artifact_meaningful,
        "manifest_path": Path("/tmp/placeholder.manifest"),
        "manifest_exists": manifest_exists,
        "healthy": healthy,
        "detail": detail,
    }


class RepairDecisionTests(unittest.TestCase):
    class _FakeLockedState:
        def __init__(self) -> None:
            self.state: dict[str, object] = {}

        def __enter__(self) -> dict[str, object]:
            return self.state

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    def test_partial_artifact_repair_expands_review_budgets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            artifact = repo_root / "docs" / "workflow" / "feature-runs" / "demo" / "tasks.md"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("x" * 1500, encoding="utf-8")
            review = artifact.parent / "reviews" / "tasks.codex.md"
            review.parent.mkdir()
            review.write_text(
                "\n".join(
                    [
                        "---",
                        'coverage_status: "partial"',
                        'coverage_note: "artifact exceeded max_artifact_chars and was narrowed"',
                        "---",
                        "body",
                    ]
                ),
                encoding="utf-8",
            )
            spec = {"path": str(review.relative_to(repo_root)), "context_paths": []}
            checkpoint = {"stage": "tasks", "max_artifact_chars": 500, "max_total_chars": 1000}

            with patch.object(REPAIR_MODULE, "REPO_ROOT", repo_root):
                expanded = REPAIR_MODULE.expanded_checkpoint_for_partial_artifact(spec, artifact, checkpoint)

            self.assertIsNotNone(expanded)
            assert expanded is not None
            self.assertGreaterEqual(expanded["max_artifact_chars"], 2500)
            self.assertGreaterEqual(expanded["max_total_chars"], 12500)

    def test_partial_context_repair_does_not_expand_artifact_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            artifact = repo_root / "docs" / "workflow" / "feature-runs" / "demo" / "tasks.md"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("x" * 1500, encoding="utf-8")
            review = artifact.parent / "reviews" / "tasks.codex.md"
            review.parent.mkdir()
            review.write_text(
                "\n".join(
                    [
                        "---",
                        'coverage_status: "partial"',
                        'coverage_note: "context exceeded max_context_chars and was narrowed"',
                        "---",
                        "body",
                    ]
                ),
                encoding="utf-8",
            )
            spec = {"path": str(review.relative_to(repo_root)), "context_paths": []}
            checkpoint = {"stage": "tasks", "max_artifact_chars": 500, "max_total_chars": 1000}

            with patch.object(REPAIR_MODULE, "REPO_ROOT", repo_root):
                expanded = REPAIR_MODULE.expanded_checkpoint_for_partial_artifact(spec, artifact, checkpoint)

            self.assertIsNone(expanded)

    def test_repair_checkpoint_args_preserves_required_reviews(self) -> None:
        manifest = {
            "required_reviews": [
                {
                    "reviewer": "gemini",
                    "lens": "risk-adversarial",
                    "context_paths": ["docs/workflow/feature-runs/feature-workflow-repair/spec.md"],
                }
            ],
            "allowed_dirty_paths": ["docs/workflow/feature-runs/feature-workflow-repair"],
            "git_base_ref": "origin/main",
            "max_artifact_chars": 123,
        }
        with patch.object(REVIEW_MODULE, "load_checkpoint_manifest", return_value=manifest), patch.object(
            REVIEW_MODULE, "load_workflow_state", return_value={"dirty_overrides": {}}
        ):
            args = MODULE.repair_checkpoint_args(
                "feature-workflow-repair",
                "plan",
                stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=False, healthy=False),
            )

        self.assertEqual(args.required_reviews, manifest["required_reviews"])
        self.assertEqual(args.base_ref, "origin/main")
        self.assertEqual(args.allow_dirty_path, ["docs/workflow/feature-runs/feature-workflow-repair"])

    def test_stage_drift_class_distinguishes_missing_artifact_and_missing_manifest(self) -> None:
        blank_spec = stage_state()
        uncheckpointed_diff = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=False, healthy=False)

        self.assertEqual(MODULE.stage_drift_class("spec", blank_spec), "missing-artifact")
        self.assertEqual(MODULE.stage_drift_class("diff", uncheckpointed_diff), "missing-manifest")

    def test_repair_is_preferred_for_meaningful_plan_artifact_without_manifest(self) -> None:
        stages = {
            "spec": stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True),
            "plan": stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=False, healthy=False),
            "tasks": stage_state(),
            "diff": stage_state(),
            "closeout": stage_state(),
        }
        action = MODULE.recommended_next_action(
            "feature-workflow-repair",
            {
                "blocked": {"active": False},
                "delivery": {},
                "parallel_analysis": {"reviewed": True, "found": False, "note": "", "updated_at": 0},
            },
            stages,
            True,
        )
        self.assertEqual(action, "run_plan_checkpoint")

    def test_blank_spec_still_looks_like_authoring_not_repair(self) -> None:
        stages = {
            "spec": stage_state(),
            "plan": stage_state(),
            "tasks": stage_state(),
            "diff": stage_state(),
            "closeout": stage_state(),
        }
        action = MODULE.recommended_next_action(
            "feature-workflow-repair",
            {
                "blocked": {"active": False},
                "delivery": {},
                "parallel_analysis": {"reviewed": True, "found": False, "note": "", "updated_at": 0},
            },
            stages,
            True,
        )
        self.assertEqual(action, "author_spec")

    def test_recommended_next_action_prefers_discovery_before_spec(self) -> None:
        stages = {
            "spec": stage_state(),
            "plan": stage_state(),
            "tasks": stage_state(),
            "diff": stage_state(),
            "closeout": stage_state(),
        }
        action = MODULE.recommended_next_action(
            "feature-workflow-discovery-shaping",
            {
                "blocked": {"active": False},
                "delivery": {},
                "discovery": {
                    "required": True,
                    "complete": False,
                    "answers": {},
                    "unresolved": [],
                    "non_goals": [],
                    "acceptance_criteria": [],
                },
            },
            stages,
            True,
        )
        self.assertEqual(action, "discover")

    def test_recommended_next_action_blocks_spec_when_unresolved_items_remain(self) -> None:
        stages = {
            "spec": stage_state(),
            "plan": stage_state(),
            "tasks": stage_state(),
            "diff": stage_state(),
            "closeout": stage_state(),
        }
        action = MODULE.recommended_next_action(
            "feature-workflow-repair",
            {
                "blocked": {"active": False},
                "delivery": {},
                "discovery": {
                    "required": False,
                    "complete": True,
                    "unresolved": [{"item": "Decide API shape", "deferred": False}],
                },
            },
            stages,
            True,
        )
        self.assertEqual(action, "discover")

    def test_blocking_unresolved_items_treats_malformed_entries_as_blocking(self) -> None:
        self.assertEqual(
            MODULE.blocking_unresolved_items({"unresolved": ["bad", {"no_item": "x"}, {"item": " "}]}),
            [
                {"item": "<malformed unresolved item>", "deferred": False, "malformed": True},
                {"item": "<malformed unresolved item>", "deferred": False, "malformed": True},
                {"item": "<malformed unresolved item>", "deferred": False, "malformed": True},
            ],
        )
        self.assertEqual(
            MODULE.blocking_unresolved_items({"unresolved": None}),
            [{"item": "<malformed discovery state>", "deferred": False, "malformed": True}],
        )

    def test_blocking_unresolved_items_requires_boolean_true_for_deferred(self) -> None:
        self.assertEqual(
            MODULE.blocking_unresolved_items(
                {
                    "unresolved": [
                        {"item": "Decide API shape", "deferred": "false"},
                        {"item": "Confirm rollout", "deferred": 1},
                    ]
                }
            ),
            [
                {"item": "Decide API shape", "deferred": "false"},
                {"item": "Confirm rollout", "deferred": 1},
            ],
        )

    def test_blocking_unresolved_items_blocks_malformed_entries_even_if_deferred(self) -> None:
        self.assertEqual(
            MODULE.blocking_unresolved_items(
                {
                    "unresolved": [
                        {"item": None, "deferred": True},
                        {"item": " ", "deferred": True},
                        {"no_item": "x", "deferred": True},
                    ]
                }
            ),
            [
                {"item": "<malformed unresolved item>", "deferred": False, "malformed": True},
                {"item": "<malformed unresolved item>", "deferred": False, "malformed": True},
                {"item": "<malformed unresolved item>", "deferred": False, "malformed": True},
            ],
        )

    def test_recommended_next_action_keeps_blocked_state_ahead_of_discovery(self) -> None:
        stages = {
            "spec": stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True),
            "plan": stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True),
            "tasks": stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True),
            "diff": stage_state(),
            "closeout": stage_state(),
        }
        action = MODULE.recommended_next_action(
            "feature-workflow-discovery-shaping",
            {
                "blocked": {"active": True},
                "delivery": {},
                "discovery": {
                    "required": True,
                    "complete": False,
                    "answers": {},
                    "unresolved": [],
                    "non_goals": [],
                    "acceptance_criteria": [],
                },
            },
            stages,
            True,
        )
        self.assertEqual(action, "mark_blocked")

    def test_diff_missing_artifact_keeps_implementation_flow_next(self) -> None:
        stages = {
            "spec": stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True),
            "plan": stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True),
            "tasks": stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True),
            "diff": stage_state(artifact_exists=False, artifact_meaningful=False, manifest_exists=False, healthy=False),
            "closeout": stage_state(),
        }
        action = MODULE.recommended_next_action(
            "feature-workflow-repair",
            {
                "blocked": {"active": False},
                "delivery": {},
                "parallel_analysis": {"reviewed": True, "found": False, "note": "", "updated_at": 0},
            },
            stages,
            True,
        )
        self.assertEqual(action, "dispatch_next_slice_to_codex")

    def test_stage_status_reports_repairable_for_meaningful_uncheckpointed_plan(self) -> None:
        state = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=False, healthy=False)
        self.assertEqual(MODULE.stage_status_label("feature-workflow-repair", "plan", state), "repairable")

    def test_stub_artifact_is_not_considered_repairable(self) -> None:
        state = stage_state(artifact_exists=True, artifact_meaningful=False, manifest_exists=False, healthy=False)
        self.assertEqual(MODULE.stage_drift_class("spec", state), "stub-artifact")
        self.assertEqual(MODULE.stage_status_label("feature-workflow-repair", "spec", state), "stub-artifact")

    def test_recommended_next_action_ignores_stub_later_stage_docs_on_fresh_run(self) -> None:
        stages = {
            "spec": stage_state(artifact_exists=True, artifact_meaningful=False, manifest_exists=False, healthy=False),
            "plan": stage_state(artifact_exists=True, artifact_meaningful=False, manifest_exists=False, healthy=False),
            "tasks": stage_state(artifact_exists=True, artifact_meaningful=False, manifest_exists=False, healthy=False),
            "diff": stage_state(),
            "closeout": stage_state(),
        }
        action = MODULE.recommended_next_action(
            "feature-workflow-repair",
            {
                "blocked": {"active": False},
                "delivery": {},
                "discovery": {
                    "required": True,
                    "complete": True,
                    "answers": {},
                    "unresolved": [],
                    "non_goals": [],
                    "acceptance_criteria": [],
                },
            },
            stages,
            True,
        )
        self.assertEqual(action, "author_spec")

    def test_recommended_next_action_blocks_stub_artifact_with_later_progress(self) -> None:
        stages = {
            "spec": stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True),
            "plan": stage_state(artifact_exists=True, artifact_meaningful=False, manifest_exists=False, healthy=False),
            "tasks": stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True),
            "diff": stage_state(),
            "closeout": stage_state(),
        }
        action = MODULE.recommended_next_action(
            "feature-workflow-repair",
            {"blocked": {"active": False}, "delivery": {}},
            stages,
            True,
        )
        self.assertEqual(action, "mark_blocked")

    def test_recommended_next_action_repairs_stale_diff_head(self) -> None:
        stages = {
            "spec": stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True),
            "plan": stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True),
            "tasks": stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True),
            "diff": stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True),
            "closeout": stage_state(),
        }
        with patch.object(
            NEXT_ACTION_MODULE,
            "diff_review_budget_state",
            return_value={"head_mismatch": True, "recorded_head_sha": "abc123", "current_head_sha": "def456"},
        ):
            action = MODULE.recommended_next_action(
                "feature-workflow-repair",
                {
                    "blocked": {"active": False},
                    "delivery": {},
                    "parallel_analysis": {"reviewed": True, "found": False, "note": "", "updated_at": 0},
                },
                stages,
                True,
            )
        self.assertEqual(action, "run_diff_checkpoint")

    def test_preferred_diff_base_ref_uses_last_reviewed_head_for_resumed_slice(self) -> None:
        with patch.object(
            STAGES_MODULE,
            "diff_review_budget_state",
            return_value={
                "artifact_exists": True,
                "head_mismatch": True,
                "recorded_head_sha": "abc123def456",
                "suggested_base_ref": "abc123def456",
            },
        ):
            self.assertEqual(MODULE.preferred_diff_base_ref("feature-workflow-repair"), "abc123def456")

    def test_preferred_diff_base_ref_keeps_explicit_request(self) -> None:
        with patch.object(STAGES_MODULE, "diff_review_budget_state", return_value={"suggested_base_ref": "abc123def456"}):
            self.assertEqual(MODULE.preferred_diff_base_ref("feature-workflow-repair", "origin/main"), "origin/main")

    def test_status_reports_resumed_diff_scope_basis(self) -> None:
        stages = {
            stage: stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True)
            for stage in ("spec", "plan", "tasks", "diff")
        }
        stages["closeout"] = stage_state()
        state = {
            "blocked": {"active": False},
            "delivery": {},
            "parallel_analysis": {"reviewed": True, "found": False, "note": "", "updated_at": 0},
        }
        diff_budget = {
            "artifact_exists": True,
            "artifact_bytes": 512,
            "large_artifact": False,
            "recorded_base_ref": "origin/main",
            "recorded_base_sha": "d3335ded7c643eda3d4ad7c2ac730325ff394d2c",
            "recorded_head_sha": "abc123def4567890",
            "current_head_sha": "fed456cba9876543",
            "head_mismatch": True,
            "scope_basis": "last-reviewed-head",
            "suggested_base_ref": "abc123def4567890",
            "artifact_changed_since_codex": False,
        }
        buffer = io.StringIO()
        with patch.object(CMD_STATUS_MODULE, "ensure_sync"), patch.object(
            CMD_STATUS_MODULE, "load_workflow_state", return_value=state
        ), patch.object(
            CMD_STATUS_MODULE, "stage_manifest_state", side_effect=lambda _slug, stage: stages[stage]
        ), patch.object(
            CMD_STATUS_MODULE, "reconciliation_state", return_value=(True, "")
        ), patch.object(
            CMD_STATUS_MODULE, "current_branch_name", return_value="feature-branch"
        ), patch.object(
            CMD_STATUS_MODULE, "upstream_branch_name", return_value="origin/feature-branch"
        ), patch.object(
            CMD_STATUS_MODULE, "diff_review_budget_state", return_value=diff_budget
        ), redirect_stdout(buffer):
            MODULE.command_status(SimpleNamespace(slug="feature-workflow-repair"))

        output = buffer.getvalue()
        self.assertIn("diff-review-budget:", output)
        self.assertIn("recorded-base: origin/main [d3335ded7c64]", output)
        self.assertIn("scope-basis: last-reviewed-head [abc123def456]", output)

    def test_status_reports_discovery_progress(self) -> None:
        stages = {
            stage: stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True)
            for stage in ("spec", "plan", "tasks", "diff")
        }
        stages["closeout"] = stage_state()
        buffer = io.StringIO()
        discovery = {
            "required": True,
            "complete": False,
            "question_count": 5,
            "asked_count": 2,
            "questions": [
                {
                    "question": "What is the user-facing behavior?",
                    "recommendation": "Keep it minimal",
                    "rationale": "We only need the first enforceable slice right now.",
                    "updated_at": 1,
                }
            ],
            "assumptions": ["Faster clarity beats extra approval ceremony."],
            "summary": "Discovery is still in progress.",
            "updated_at": 1,
            "answers": {},
            "unresolved": [],
            "non_goals": [],
            "acceptance_criteria": [],
        }
        with patch.object(CMD_STATUS_MODULE, "ensure_sync"), patch.object(
            CMD_STATUS_MODULE,
            "load_workflow_state",
            return_value={"blocked": {"active": False}, "delivery": {}, "discovery": discovery},
        ), patch.object(
            CMD_STATUS_MODULE, "stage_manifest_state", side_effect=lambda _slug, stage: stages[stage]
        ), patch.object(
            CMD_STATUS_MODULE, "reconciliation_state", return_value=(True, "")
        ), patch.object(
            CMD_STATUS_MODULE, "current_branch_name", return_value="feature-branch"
        ), patch.object(
            CMD_STATUS_MODULE, "upstream_branch_name", return_value="origin/feature-branch"
        ), patch.object(
            CMD_STATUS_MODULE, "diff_review_budget_state", return_value={"artifact_exists": False}
        ), patch.object(
            CMD_STATUS_MODULE, "discovery_state", return_value=discovery
        ), redirect_stdout(buffer):
            MODULE.command_status(SimpleNamespace(slug="feature-workflow-discovery-shaping"))

        output = buffer.getvalue()
        self.assertIn("discovery:", output)
        self.assertIn("- required: yes", output)
        self.assertIn("- complete: no", output)
        self.assertIn("- question-count: 5", output)
        self.assertIn("- asked-count: 2", output)
        self.assertIn("- remaining: 3", output)

    def test_status_reports_blocking_unresolved_discovery_items(self) -> None:
        stages = {
            stage: stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True)
            for stage in ("spec", "plan", "tasks", "diff")
        }
        stages["closeout"] = stage_state()
        buffer = io.StringIO()
        discovery = {
            "required": True,
            "complete": True,
            "question_count": 2,
            "asked_count": 2,
            "questions": [],
            "assumptions": [],
            "summary": "",
            "updated_at": 1,
            "answers": {},
            "unresolved": [
                {"item": "Decide API shape", "deferred": False},
                {"item": "Confirm rollout plan", "deferred": True},
            ],
            "non_goals": [],
            "acceptance_criteria": [],
        }
        with patch.object(CMD_STATUS_MODULE, "ensure_sync"), patch.object(
            CMD_STATUS_MODULE,
            "load_workflow_state",
            return_value={"blocked": {"active": False}, "delivery": {}, "discovery": discovery},
        ), patch.object(
            CMD_STATUS_MODULE, "stage_manifest_state", side_effect=lambda _slug, stage: stages[stage]
        ), patch.object(
            CMD_STATUS_MODULE, "reconciliation_state", return_value=(True, "")
        ), patch.object(
            CMD_STATUS_MODULE, "current_branch_name", return_value="feature-branch"
        ), patch.object(
            CMD_STATUS_MODULE, "upstream_branch_name", return_value="origin/feature-branch"
        ), patch.object(
            CMD_STATUS_MODULE, "diff_review_budget_state", return_value={"artifact_exists": False}
        ), patch.object(
            CMD_STATUS_MODULE, "discovery_state", return_value=discovery
        ), redirect_stdout(buffer):
            MODULE.command_status(SimpleNamespace(slug="feature-workflow-discovery-shaping"))

        output = buffer.getvalue()
        self.assertIn("- unresolved-open: 1", output)
        self.assertIn("- unresolved-deferred: 1", output)
        self.assertIn("- action: resolve or defer unresolved items before spec", output)
        self.assertIn("next-action: discover", output)

    def test_status_handles_malformed_unresolved_state_as_blocking(self) -> None:
        stages = {
            stage: stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True)
            for stage in ("spec", "plan", "tasks", "diff")
        }
        stages["closeout"] = stage_state()
        buffer = io.StringIO()
        discovery = {
            "required": True,
            "complete": True,
            "question_count": 0,
            "asked_count": 0,
            "questions": [],
            "assumptions": [],
            "summary": "",
            "updated_at": 1,
            "answers": {},
            "unresolved": None,
            "non_goals": [],
            "acceptance_criteria": [],
        }
        with patch.object(CMD_STATUS_MODULE, "ensure_sync"), patch.object(
            CMD_STATUS_MODULE,
            "load_workflow_state",
            return_value={"blocked": {"active": False}, "delivery": {}, "discovery": discovery},
        ), patch.object(
            CMD_STATUS_MODULE, "stage_manifest_state", side_effect=lambda _slug, stage: stages[stage]
        ), patch.object(
            CMD_STATUS_MODULE, "reconciliation_state", return_value=(True, "")
        ), patch.object(
            CMD_STATUS_MODULE, "current_branch_name", return_value="feature-branch"
        ), patch.object(
            CMD_STATUS_MODULE, "upstream_branch_name", return_value="origin/feature-branch"
        ), patch.object(
            CMD_STATUS_MODULE, "diff_review_budget_state", return_value={"artifact_exists": False}
        ), patch.object(
            CMD_STATUS_MODULE, "discovery_state", return_value=discovery
        ), redirect_stdout(buffer):
            MODULE.command_status(SimpleNamespace(slug="feature-workflow-discovery-shaping"))

        output = buffer.getvalue()
        self.assertIn("- unresolved-open: 1", output)
        self.assertIn("- action: use discover --clear to repair malformed discovery state", output)

    def test_recommended_next_action_blocks_closeout_on_stale_delivery_head(self) -> None:
        stages = {
            stage: stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True)
            for stage in ("spec", "plan", "tasks", "diff")
        }
        stages["closeout"] = stage_state()
        with tempfile.TemporaryDirectory() as tmp:
            # All tasks checked off — no remaining slices to implement.
            workflow_root = Path(tmp)
            (workflow_root / "tasks.md").write_text("- [x] Task 1\n- [x] Task 2\n", encoding="utf-8")
            with patch.object(NEXT_ACTION_MODULE, "workflow_dir", return_value=workflow_root), \
                    patch.object(NEXT_ACTION_MODULE, "diff_review_budget_state", return_value={"head_mismatch": False}):
                action = MODULE.recommended_next_action(
                    "feature-workflow-repair",
                    {
                        "blocked": {"active": False},
                        "delivery": {"pr_url": "https://example.com", "head_mismatch": True},
                        "parallel_analysis": {"reviewed": True, "found": False, "note": "", "updated_at": 0},
                    },
                    stages,
                    True,
                )
        self.assertEqual(action, "deliver")

    def test_command_deliver_blocks_when_reviewed_diff_head_moves(self) -> None:
        args = SimpleNamespace(
            slug="feature-workflow-repair",
            create_pr=False,
            draft=False,
            base=None,
            title=None,
            watch_ci=False,
            interval=10,
            merge_when_green=False,
            auto_merge=False,
            dry_run=False,
            override_judges=False,
            reason=None,
            resume_merge_wait=False,
            refresh=False,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            for stage in ("spec", "plan", "tasks", "diff"):
                (temp_root / f"{stage}.checkpoint.json").write_text("{}", encoding="utf-8")

            def manifest_path(_slug: str, stage: str) -> Path:
                return temp_root / f"{stage}.checkpoint.json"

            with patch.object(CMD_DELIVER_MODULE, "ensure_sync"), patch.object(
                CMD_DELIVER_MODULE, "command_path", return_value="/usr/bin/gh"
            ), patch.object(
                CMD_DELIVER_MODULE, "verify_checkpoint_manifest", return_value=(True, "")
            ), patch.object(
                CMD_DELIVER_MODULE, "reconciliation_state", return_value=(True, "")
            ), patch.object(
                CMD_DELIVER_MODULE,
                "diff_review_budget_state",
                return_value={"head_mismatch": True, "recorded_head_sha": "abc123", "current_head_sha": "def456"},
            ), patch.object(
                CMD_DELIVER_MODULE, "current_branch_name", return_value="feature-branch"
            ), patch.object(
                CMD_DELIVER_MODULE, "upstream_branch_name", return_value="origin/feature-branch"
            ), patch.object(
                CMD_DELIVER_MODULE, "checkpoint_manifest_path", side_effect=manifest_path
            ), patch.object(
                CMD_DELIVER_MODULE.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")
            ):
                with self.assertRaises(SystemExit) as ctx:
                    MODULE.command_deliver(args)

        self.assertIn("reviewed diff HEAD", str(ctx.exception))

    def test_command_repair_repairs_stale_diff_head(self) -> None:
        healthy = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True)
        diff_initial = stage_state(
            artifact_exists=True,
            artifact_meaningful=True,
            manifest_exists=True,
            healthy=False,
            detail="reviewed diff HEAD is stale",
        )
        diff_refreshed = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True)
        closeout = stage_state()
        call_count = {"diff": 0}

        def fake_manifest_state(_slug: str, stage: str) -> dict[str, object]:
            if stage == "diff":
                call_count["diff"] += 1
                return diff_initial if call_count["diff"] == 1 else diff_refreshed
            if stage == "closeout":
                return closeout
            return healthy

        args = SimpleNamespace(slug="feature-workflow-repair")
        with patch.object(CMD_STATUS_MODULE, "ensure_sync"), patch.object(
            CMD_STATUS_MODULE,
            "load_workflow_state",
            return_value={"blocked": {"active": False}, "delivery": {}, "checkpoint_fallback": {}},
        ), patch.object(
            CMD_STATUS_MODULE, "stage_manifest_state", side_effect=fake_manifest_state
        ), patch.object(
            CMD_STATUS_MODULE,
            "stage_drift_class",
            side_effect=lambda stage, _state: "unhealthy-manifest" if stage == "diff" else "healthy",
        ), patch.object(
            CMD_STATUS_MODULE,
            "stage_repairable",
            side_effect=lambda _slug, stage, _state: stage == "diff",
        ), patch.object(
            CMD_STATUS_MODULE, "stage_review_inventory", return_value=([], [])
        ), patch.object(
            CMD_STATUS_MODULE, "repair_checkpoint_args", return_value=SimpleNamespace(slug="feature-workflow-repair", stage="diff")
        ), patch.object(
            CMD_CHECKPOINT_MODULE, "command_checkpoint", return_value=0
        ) as checkpoint_mock, patch.object(
            CMD_STATUS_MODULE, "reconciliation_state", return_value=(True, "")
        ), patch.object(
            CMD_STATUS_MODULE, "recommended_next_action", return_value="deliver"
        ), patch.object(
            CMD_STATUS_MODULE, "stage_status_label", return_value="healthy"
        ), patch.object(
            CMD_STATUS_MODULE, "trim_detail", side_effect=lambda s: s
        ):
            exit_code = MODULE.command_repair(args)

        self.assertEqual(exit_code, 0)
        checkpoint_mock.assert_called_once()

    def test_command_checkpoint_blocks_spec_until_discovery_completes(self) -> None:
        args = SimpleNamespace(
            slug="feature-workflow-discovery-shaping",
            stage="spec",
            path=[],
            artifact="",
            base_ref=None,
            context=[],
            allow_dirty_path=[],
            max_artifact_chars=None,
            max_context_chars=None,
            max_total_chars=None,
            gemini_timeout_seconds=120,
            gemini_retries=1,
            repair_timeout_seconds=30,
            fallback=False,
            use_existing_artifact=False,
            allow_large_diff_rerun=False,
            required_reviews=None,
        )
        with patch.object(CMD_CHECKPOINT_MODULE, "ensure_sync"), patch.object(
            CMD_CHECKPOINT_MODULE, "prerequisite_failure", return_value=None
        ), patch.object(
            CMD_CHECKPOINT_MODULE,
            "discovery_state",
            return_value={
                "required": True,
                "complete": False,
                "question_count": 5,
                "asked_count": 2,
                "answers": {},
                "unresolved": [],
                "non_goals": [],
                "acceptance_criteria": [],
            },
        ):
            with self.assertRaises(SystemExit) as ctx:
                MODULE.command_checkpoint(args)

        self.assertIn("discovery", str(ctx.exception))

    def test_command_checkpoint_blocks_spec_until_unresolved_items_are_cleared(self) -> None:
        args = SimpleNamespace(
            slug="feature-workflow-discovery-shaping",
            stage="spec",
            path=[],
            artifact="",
            base_ref=None,
            context=[],
            allow_dirty_path=[],
            max_artifact_chars=None,
            max_context_chars=None,
            max_total_chars=None,
            gemini_timeout_seconds=120,
            gemini_retries=1,
            repair_timeout_seconds=30,
            fallback=False,
            use_existing_artifact=False,
            allow_large_diff_rerun=False,
            required_reviews=None,
        )
        with patch.object(CMD_CHECKPOINT_MODULE, "ensure_sync"), patch.object(
            CMD_CHECKPOINT_MODULE, "prerequisite_failure", return_value=None
        ), patch.object(
            CMD_CHECKPOINT_MODULE,
            "discovery_state",
            return_value={
                "required": True,
                "complete": True,
                "question_count": 5,
                "asked_count": 5,
                "answers": {},
                "unresolved": [{"item": "Decide API shape", "deferred": False}],
                "non_goals": [],
                "acceptance_criteria": [],
            },
        ):
            with self.assertRaises(SystemExit) as ctx:
                MODULE.command_checkpoint(args)

        self.assertIn("unresolved", str(ctx.exception))

    def test_command_discover_records_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            state_path = temp_root / "state.json"
            args = SimpleNamespace(
                slug="feature-workflow-discovery-shaping",
                required=True,
                count=5,
                question="Should this slice prioritize speed or coverage?",
                recommendation="Prioritize speed",
                rationale="The plan already says to avoid extra approval ceremony.",
                assumption=["Keep the first slice small."],
                summary="Five questions are expected for the current ambiguous request.",
                complete=False,
            )
            with patch.object(MODULE, "ensure_sync"), patch.object(
                FACTORY_STATE, "factory_state_path", return_value=state_path
            ):
                exit_code = MODULE.command_discover(args)

            self.assertEqual(exit_code, 0)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            discovery = saved["discovery"]
            self.assertTrue(discovery["required"])
            self.assertFalse(discovery["complete"])
            self.assertEqual(discovery["question_count"], 5)
            self.assertEqual(discovery["asked_count"], 1)
            self.assertEqual(discovery["questions"][0]["question"], args.question)
            self.assertIn("Keep the first slice small.", discovery["assumptions"])
            self.assertIn("Five questions are expected", discovery["summary"])
            self.assertEqual(discovery["version"], 2)

    def test_command_discover_rejects_force_complete_with_unresolved_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "discovery": {
                            "version": 2,
                            "required": True,
                            "complete": False,
                            "question_count": 2,
                            "asked_count": 1,
                            "questions": [],
                            "assumptions": [],
                            "summary": "",
                            "updated_at": 1,
                            "answers": {},
                            "unresolved": [{"item": "Decide API shape", "deferred": False}],
                            "non_goals": [],
                            "acceptance_criteria": [],
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = self._discover_args(force_complete=True)
            with patch.object(MODULE, "ensure_sync"), patch.object(
                FACTORY_STATE, "factory_state_path", return_value=state_path
            ):
                with self.assertRaises(SystemExit) as ctx:
                    MODULE.command_discover(args)

        self.assertIn("unresolved", str(ctx.exception))

    def test_command_discover_can_resolve_then_complete_in_same_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "discovery": {
                            "version": 2,
                            "required": True,
                            "complete": False,
                            "question_count": 2,
                            "asked_count": 2,
                            "questions": [],
                            "assumptions": [],
                            "summary": "",
                            "updated_at": 1,
                            "answers": {},
                            "unresolved": [{"item": "Decide API shape", "deferred": False}],
                            "non_goals": [],
                            "acceptance_criteria": [],
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = self._discover_args(resolve="Decide API shape", complete=True)
            with patch.object(MODULE, "ensure_sync"), patch.object(
                FACTORY_STATE, "factory_state_path", return_value=state_path
            ):
                exit_code = MODULE.command_discover(args)

            self.assertEqual(exit_code, 0)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(saved["discovery"]["complete"])
            self.assertEqual(saved["discovery"]["unresolved"], [])

    def test_command_discover_rejects_malformed_state_with_clear_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "discovery": {
                            "version": 2,
                            "required": True,
                            "complete": False,
                            "question_count": 2,
                            "asked_count": 2,
                            "questions": [],
                            "assumptions": [],
                            "summary": "",
                            "updated_at": 1,
                            "answers": {},
                            "unresolved": None,
                            "non_goals": [],
                            "acceptance_criteria": [],
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = self._discover_args(complete=True)
            with patch.object(MODULE, "ensure_sync"), patch.object(
                FACTORY_STATE, "factory_state_path", return_value=state_path
            ):
                with self.assertRaises(SystemExit) as ctx:
                    MODULE.command_discover(args)

        self.assertIn("discover --clear", str(ctx.exception))

    def test_command_discover_clear_resets_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            state_path = temp_root / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "discovery": {
                            "version": 1,
                            "required": True,
                            "complete": False,
                            "question_count": 5,
                            "asked_count": 3,
                            "questions": [{"question": "a", "recommendation": "b", "rationale": "c"}],
                            "assumptions": ["x"],
                            "summary": "old",
                            "updated_at": 1,
                            "answers": {},
                            "unresolved": [],
                            "non_goals": [],
                            "acceptance_criteria": [],
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                slug="feature-workflow-discovery-shaping",
                required=False,
                count=None,
                question=None,
                recommendation=None,
                rationale=None,
                assumption=[],
                summary=None,
                complete=False,
                force_complete=False,
                clear=True,
            )
            with patch.object(MODULE, "ensure_sync"), patch.object(
                FACTORY_STATE, "factory_state_path", return_value=state_path
            ):
                exit_code = MODULE.command_discover(args)

            self.assertEqual(exit_code, 0)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            discovery = saved["discovery"]
            self.assertTrue(discovery["required"])
            self.assertFalse(discovery["complete"])
            self.assertEqual(discovery["question_count"], 5)
            self.assertEqual(discovery["asked_count"], 3)
            self.assertEqual(discovery["questions"], [{"question": "a", "recommendation": "b", "rationale": "c"}])
            self.assertEqual(discovery["assumptions"], ["x"])
            self.assertEqual(discovery["summary"], "old")
            self.assertEqual(discovery["answers"], {})
            self.assertEqual(discovery["non_goals"], [])
            self.assertEqual(discovery["acceptance_criteria"], [])
            self.assertEqual(discovery["unresolved"], [])

    def test_command_discover_rejects_premature_completion_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            state_path = temp_root / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "discovery": {
                            "version": 1,
                            "required": True,
                            "complete": False,
                            "question_count": 5,
                            "asked_count": 2,
                            "questions": [],
                            "assumptions": [],
                            "summary": "",
                            "updated_at": 1,
                            "answers": {},
                            "unresolved": [],
                            "non_goals": [],
                            "acceptance_criteria": [],
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                slug="feature-workflow-discovery-shaping",
                required=False,
                count=None,
                question=None,
                recommendation=None,
                rationale=None,
                assumption=[],
                summary=None,
                complete=True,
                force_complete=False,
                clear=False,
            )
            with patch.object(MODULE, "ensure_sync"), patch.object(
                FACTORY_STATE, "factory_state_path", return_value=state_path
            ):
                with self.assertRaises(SystemExit) as ctx:
                    MODULE.command_discover(args)

        self.assertIn("force-complete", str(ctx.exception))

    def _write_discovery_state(self, path: Path, **updates) -> None:
        discovery = FACTORY_STATE.default_discovery_state()
        discovery.update(updates)
        path.write_text(json.dumps({"discovery": discovery}), encoding="utf-8")

    def _discover_args(self, **overrides) -> SimpleNamespace:
        base = {
            "slug": "feature-workflow-discovery-shaping",
            "required": False,
            "count": None,
            "question": None,
            "recommendation": None,
            "rationale": None,
            "assumption": [],
            "summary": None,
            "complete": False,
            "force_complete": False,
            "clear": False,
            "unresolved": None,
            "resolve": None,
            "defer": None,
            "non_goal": None,
            "acceptance_criteria": None,
            "answer": None,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_command_discover_unresolved_adds_item_with_deferred_false(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            args = self._discover_args(unresolved="Need API contract")
            with patch.object(MODULE, "ensure_sync"), patch.object(
                FACTORY_STATE, "factory_state_path", return_value=state_path
            ):
                exit_code = MODULE.command_discover(args)

            self.assertEqual(exit_code, 0)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                saved["discovery"]["unresolved"],
                [{"item": "Need API contract", "deferred": False}],
            )

    def test_command_discover_resolve_removes_item_by_exact_text_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            self._write_discovery_state(
                state_path,
                required=True,
                complete=False,
                unresolved=[
                    {"item": "Keep this", "deferred": False},
                    {"item": "Remove this", "deferred": False},
                ],
            )
            args = self._discover_args(resolve="Remove this")
            with patch.object(MODULE, "ensure_sync"), patch.object(
                FACTORY_STATE, "factory_state_path", return_value=state_path
            ):
                exit_code = MODULE.command_discover(args)

            self.assertEqual(exit_code, 0)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual([u["item"] for u in saved["discovery"]["unresolved"]], ["Keep this"])

    def test_command_discover_resolve_noop_when_item_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            self._write_discovery_state(
                state_path,
                required=True,
                complete=False,
                unresolved=[{"item": "Keep this", "deferred": False}],
            )
            args = self._discover_args(resolve="Missing item")
            with patch.object(MODULE, "ensure_sync"), patch.object(
                FACTORY_STATE, "factory_state_path", return_value=state_path
            ):
                exit_code = MODULE.command_discover(args)

            self.assertEqual(exit_code, 0)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual([u["item"] for u in saved["discovery"]["unresolved"]], ["Keep this"])

    def test_command_discover_defer_marks_matching_item_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            self._write_discovery_state(
                state_path,
                required=True,
                complete=False,
                unresolved=[{"item": "Need follow-up", "deferred": False}],
            )
            args = self._discover_args(defer="Need follow-up")
            with patch.object(MODULE, "ensure_sync"), patch.object(
                FACTORY_STATE, "factory_state_path", return_value=state_path
            ):
                exit_code = MODULE.command_discover(args)

            self.assertEqual(exit_code, 0)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                saved["discovery"]["unresolved"],
                [{"item": "Need follow-up", "deferred": True}],
            )

    def test_command_discover_non_goal_appends_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            args = self._discover_args(non_goal=["Avoid broad scope"])
            with patch.object(MODULE, "ensure_sync"), patch.object(
                FACTORY_STATE, "factory_state_path", return_value=state_path
            ):
                first_exit = MODULE.command_discover(args)
                second_exit = MODULE.command_discover(args)

            self.assertEqual(first_exit, 0)
            self.assertEqual(second_exit, 0)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["discovery"]["non_goals"], ["Avoid broad scope"])

    def test_command_discover_acceptance_criteria_appends_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            args = self._discover_args(acceptance_criteria=["Clear owner for each decision"])
            with patch.object(MODULE, "ensure_sync"), patch.object(
                FACTORY_STATE, "factory_state_path", return_value=state_path
            ):
                first_exit = MODULE.command_discover(args)
                second_exit = MODULE.command_discover(args)

            self.assertEqual(first_exit, 0)
            self.assertEqual(second_exit, 0)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["discovery"]["acceptance_criteria"], ["Clear owner for each decision"])

    def test_command_discover_answer_records_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            args = self._discover_args(answer=("What is the goal?", "Ship the first slice"))
            with patch.object(MODULE, "ensure_sync"), patch.object(
                FACTORY_STATE, "factory_state_path", return_value=state_path
            ):
                exit_code = MODULE.command_discover(args)

            self.assertEqual(exit_code, 0)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                saved["discovery"]["answers"],
                {"What is the goal?": "Ship the first slice"},
            )

    def test_command_deliver_dry_run_does_not_mutate_delivery_state(self) -> None:
        args = SimpleNamespace(
            slug="feature-workflow-repair",
            create_pr=True,
            draft=False,
            base=None,
            title=None,
            watch_ci=False,
            interval=10,
            merge_when_green=False,
            auto_merge=False,
            dry_run=True,
            override_judges=False,
            reason=None,
            resume_merge_wait=False,
            refresh=False,
        )
        fake_manifest_path = MagicMock()
        fake_manifest_path.exists.return_value = True
        with patch.object(CMD_DELIVER_MODULE, "ensure_sync"), patch.object(
            CMD_DELIVER_MODULE, "command_path", return_value="/usr/bin/gh"
        ), patch.object(
            CMD_DELIVER_MODULE, "checkpoint_manifest_path", return_value=fake_manifest_path
        ), patch.object(
            CMD_DELIVER_MODULE, "verify_checkpoint_manifest", return_value=(True, "")
        ), patch.object(
            CMD_DELIVER_MODULE, "reconciliation_state", return_value=(True, "")
        ), patch.object(
            CMD_DELIVER_MODULE, "current_branch_name", return_value="feature-branch"
        ), patch.object(
            CMD_DELIVER_MODULE, "upstream_branch_name", return_value="origin/feature-branch"
        ), patch.object(
            CMD_DELIVER_MODULE, "current_pr_payload", return_value=None
        ), patch.object(
            CMD_DELIVER_MODULE, "update_workflow_state"
        ) as update_mock, patch.object(
            CMD_DELIVER_MODULE.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")
        ), patch.object(
            CMD_DELIVER_MODULE, "diff_review_budget_state", return_value={"head_mismatch": False}
        ), patch.object(
            CMD_DELIVER_MODULE, "required_check_summary", return_value=("unknown", [], "")
        ):
            exit_code = MODULE.command_deliver(args)

        self.assertEqual(exit_code, 0)
        update_mock.assert_not_called()

    def test_compose_closeout_text_preserves_existing_narrative(self) -> None:
        existing = "# Closeout: feature-workflow-repair\n\nThis is the authored summary.\n"
        inventory = "## Workflow Inventory\n\n# Closeout: feature-workflow-repair\n"
        composed = MODULE.compose_closeout_text(existing, inventory)

        self.assertTrue(composed.startswith(existing.rstrip()))
        self.assertIn("## Workflow Inventory", composed)
        self.assertIn("This is the authored summary.", composed)

    def test_compose_closeout_text_refreshes_existing_inventory(self) -> None:
        existing = (
            "# Closeout: feature-workflow-repair\n\n"
            "This is the authored summary.\n\n"
            "## Workflow Inventory\n\n"
            "- stale inventory entry\n"
        )
        inventory = "## Workflow Inventory\n\n- refreshed inventory entry\n"
        composed = MODULE.compose_closeout_text(existing, inventory)

        self.assertIn("This is the authored summary.", composed)
        self.assertNotIn("stale inventory entry", composed)
        self.assertIn("refreshed inventory entry", composed)

    # NOTE: the former test_command_closeout_uses_active_review_chain_only was
    # removed — it asserted closeout called the deliver module's
    # gather_all_review_paths, but closeout is now a standalone command
    # (command_standalone_closeout) that scans reviews via _scan_reviews. Current
    # closeout behavior is covered by tests/test_factory_cmd_closeout.py.

    def test_record_checkpoint_fallback_persists_state(self) -> None:
        captured: dict[str, object] = {}

        def mutate(state: dict) -> None:
            captured.update(state)

        with patch.object(MODULE, "update_workflow_state", side_effect=lambda slug, mutate_fn: mutate_fn(captured) or captured):
            state = MODULE.record_checkpoint_fallback("feature-workflow-repair", "diff", "repair exited 1")

        self.assertTrue(state["checkpoint_fallback"]["used"])
        self.assertEqual(state["checkpoint_fallback"]["stage"], "diff")
        self.assertEqual(state["checkpoint_fallback"]["reason"], "repair exited 1")

    def test_command_checkpoint_does_not_record_failed_fallback(self) -> None:
        args = SimpleNamespace(
            slug="feature-workflow-repair",
            stage="diff",
            path=[],
            artifact="",
            base_ref=None,
            context=[],
            allow_dirty_path=[],
            max_artifact_chars=None,
            max_context_chars=None,
            max_total_chars=None,
            gemini_timeout_seconds=120,
            gemini_retries=1,
            repair_timeout_seconds=30,
            fallback=True,
            json=False,
            use_existing_artifact=True,
            allow_large_diff_rerun=False,
            required_reviews=None,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            workflow_root = temp_root / "workflow"
            reviews_root = workflow_root / "reviews"
            reviews_root.mkdir(parents=True, exist_ok=True)
            artifact_path = reviews_root / "implementation.diff.patch"
            artifact_path.write_text("diff --git a/foo b/foo\n", encoding="utf-8")
            manifest_path = reviews_root / "diff.checkpoint.json"

            with patch.object(CMD_CHECKPOINT_MODULE, "ensure_sync"), patch.object(
                CMD_CHECKPOINT_MODULE, "workflow_dir", return_value=workflow_root
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "reviews_dir", return_value=reviews_root
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "default_artifact_path", return_value=artifact_path
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "checkpoint_manifest_path", return_value=manifest_path
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "prerequisite_failure", return_value=None
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "resolved_review_policy", return_value={
                    "sensitive": False,
                    "large_structural": False,
                    "performance_sensitive": False,
                    "extra_gemini_lenses": [],
                }
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "checkpoint_manifest", return_value={"stage": "diff", "required_reviews": []}
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "atomic_json_write"
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "update_workflow_state", return_value={}
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "diff_review_budget_state",
                return_value={
                    "artifact_exists": True,
                    "artifact_bytes": 20,
                    "large_artifact": False,
                    "recorded_base_ref": "",
                    "recorded_base_sha": "",
                    "recorded_head_sha": "",
                    "current_head_sha": "",
                    "head_mismatch": False,
                    "scope_basis": "branch-merge-base",
                    "suggested_base_ref": "",
                    "artifact_changed_since_codex": False,
                },
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "record_checkpoint_fallback"
            ) as record_fallback, patch.object(
                CMD_CHECKPOINT_MODULE, "run_checkpoint_fallback", return_value=(False, "boom")
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "with_locked_state", return_value=self._FakeLockedState()
            ), patch.object(
                CMD_CHECKPOINT_MODULE.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="repair failed")
            ):
                exit_code = MODULE.command_checkpoint(args)

        self.assertEqual(exit_code, 1)
        record_fallback.assert_not_called()

    def test_run_checkpoint_fallback_invokes_direct_review_runners(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            artifact = temp_root / "artifact.diff.patch"
            artifact.write_text("diff --git a/foo b/foo\n", encoding="utf-8")
            review_a = temp_root / "diff.gemini.risk.review.md"
            review_b = temp_root / "diff.codex.feasibility.review.md"
            manifest = {
                "artifact_path": str(artifact),
                "stage": "diff",
                "required_reviews": [
                    {
                        "reviewer": "gemini",
                        "lens": "risk-adversarial",
                        "path": str(review_a),
                        "context_paths": [],
                        "model": "gemini-2.5-pro",
                    },
                    {
                        "reviewer": "codex",
                        "lens": "feasibility-adversarial",
                        "path": str(review_b),
                        "context_paths": [],
                        "model": "gpt-5.4-mini",
                    },
                ],
            }
            manifest_path = temp_root / "spec.checkpoint.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd, **kwargs):
                commands.append(list(cmd))
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch.object(MODULE.subprocess, "run", side_effect=fake_run):
                ok, detail = MODULE.run_checkpoint_fallback(manifest_path, MODULE.REPO_ROOT, 120, 1)

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        joined = "\n".join(" ".join(cmd) for cmd in commands)
        self.assertIn("run_gemini_review.py", joined)
        self.assertIn("run_codex_review.py", joined)
        self.assertIn("verify_review_checkpoint.py", joined)

    def test_command_checkpoint_uses_resumed_diff_base_ref(self) -> None:
        args = SimpleNamespace(
            slug="feature-workflow-repair",
            stage="diff",
            path=[],
            artifact="",
            base_ref=None,
            context=[],
            allow_dirty_path=[],
            max_artifact_chars=None,
            max_context_chars=None,
            max_total_chars=None,
            gemini_timeout_seconds=120,
            gemini_retries=1,
            repair_timeout_seconds=30,
            fallback=False,
            json=False,
            use_existing_artifact=False,
            allow_large_diff_rerun=False,
            required_reviews=None,
        )
        commands: list[list[str]] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            workflow_root = temp_root / "workflow"
            reviews_root = workflow_root / "reviews"
            reviews_root.mkdir(parents=True, exist_ok=True)
            scope_manifest = workflow_root / "scope.json"
            scope_manifest.write_text(json.dumps({"paths": ["docs"], "allowed_dirty_paths": []}), encoding="utf-8")
            artifact_path = reviews_root / "implementation.diff.patch"
            manifest_path = reviews_root / "diff.checkpoint.json"

            def fake_run(cmd, **kwargs):
                commands.append(list(cmd))
                cmd_text = " ".join(cmd)
                if "write_canonical_diff.py" in cmd_text:
                    artifact_path.write_text("diff --git a/foo b/foo\n", encoding="utf-8")
                    artifact_path.with_suffix(".patch.json").write_text(
                        json.dumps(
                            {
                                "repo_root": ".",
                                "git_head_sha": "fed456cba9876543",
                                "git_base_ref": "abc123def4567890",
                                "git_base_sha": "1111111111111111",
                                "paths": ["docs"],
                                "allowed_dirty_paths": [],
                                "untracked_files_included": [],
                                "artifact_path": str(artifact_path),
                                "allow_empty_diff": False,
                            }
                        ),
                        encoding="utf-8",
                    )
                    return SimpleNamespace(returncode=0, stdout=str(artifact_path), stderr="")
                if "repair_review_checkpoint.py" in cmd_text:
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                raise AssertionError(f"unexpected command: {cmd}")

            with patch.object(CMD_CHECKPOINT_MODULE, "ensure_sync"), patch.object(
                CMD_CHECKPOINT_MODULE, "workflow_dir", return_value=workflow_root
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "reviews_dir", return_value=reviews_root
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "scope_manifest_path", return_value=scope_manifest
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "default_artifact_path", return_value=artifact_path
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "checkpoint_manifest_path", return_value=manifest_path
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "prerequisite_failure", return_value=None
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "resolved_review_policy", return_value={
                    "sensitive": False,
                    "large_structural": False,
                    "performance_sensitive": False,
                    "extra_gemini_lenses": [],
                }
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "diff_review_budget_state",
                return_value={
                    "artifact_exists": True,
                    "artifact_bytes": 20,
                    "large_artifact": False,
                    "recorded_base_ref": "origin/main",
                    "recorded_base_sha": "d3335ded7c643eda3d4ad7c2ac730325ff394d2c",
                    "recorded_head_sha": "abc123def4567890",
                    "current_head_sha": "fed456cba9876543",
                    "head_mismatch": True,
                    "scope_basis": "last-reviewed-head",
                    "suggested_base_ref": "abc123def4567890",
                    "artifact_changed_since_codex": False,
                },
            ), patch.object(
                STAGES_MODULE, "diff_review_budget_state",
                return_value={
                    "artifact_exists": True,
                    "artifact_bytes": 20,
                    "large_artifact": False,
                    "recorded_base_ref": "origin/main",
                    "recorded_base_sha": "d3335ded7c643eda3d4ad7c2ac730325ff394d2c",
                    "recorded_head_sha": "abc123def4567890",
                    "current_head_sha": "fed456cba9876543",
                    "head_mismatch": True,
                    "scope_basis": "last-reviewed-head",
                    "suggested_base_ref": "abc123def4567890",
                    "artifact_changed_since_codex": False,
                },
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "required_reviews", return_value=[]
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "update_workflow_state", return_value={}
            ), patch.object(
                CMD_CHECKPOINT_MODULE, "with_locked_state", return_value=self._FakeLockedState()
            ), patch.object(
                CMD_CHECKPOINT_MODULE.subprocess, "run", side_effect=fake_run
            ):
                exit_code = MODULE.command_checkpoint(args)

        self.assertEqual(exit_code, 0)
        joined = "\n".join(" ".join(cmd) for cmd in commands)
        self.assertIn("--base-ref", joined)
        self.assertIn("abc123def4567890", joined)


class ReviewAttemptTelemetryTests(unittest.TestCase):
    def test_review_attempt_record_infers_slug_and_omits_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            review_path = repo_root / "docs" / "workflow" / "feature-runs" / "demo" / "reviews" / "spec.codex.md"
            review_path.parent.mkdir(parents=True)

            record = ATTEMPTS_MODULE.review_attempt_record(
                repo_root=repo_root,
                reviewer="codex",
                model="gpt-test",
                stage="spec",
                lens="risk",
                artifact_chars=100,
                context_chars=20,
                total_chars=120,
                max_artifact_chars=1000,
                max_context_chars=1000,
                max_total_chars=2000,
                coverage_status="full",
                coverage_note="",
                result="passed",
                exit_code=0,
                duration_seconds=1.23456,
                artifact_sha256="abc123",
                review_path=review_path,
            )

        self.assertEqual(record["slug"], "demo")
        self.assertEqual(record["review_path"], "docs/workflow/feature-runs/demo/reviews/spec.codex.md")
        self.assertEqual(record["duration_seconds"], 1.235)
        self.assertNotIn("prompt", record)
        self.assertNotIn("artifact_text", record)
        self.assertNotIn("review_body", record)

    def test_append_review_attempt_writes_central_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            record = {"slug": "demo", "result": "passed"}

            ATTEMPTS_MODULE.append_review_attempt(record, repo_root)

            log_path = repo_root / "docs" / "workflow" / "operations" / "review-attempts.jsonl"
            self.assertTrue(log_path.exists())
            self.assertEqual(json.loads(log_path.read_text(encoding="utf-8")), record)


class CheckpointMarkerTests(unittest.TestCase):
    """Tests for parse_checkpoint_markers, checkpoint_progress_state, and
    the checkpoint-scoped diff base logic."""

    def test_parse_checkpoint_markers_returns_zero_when_no_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = Path(tmp) / "tasks.md"
            tasks.write_text("# Tasks\n\n- [ ] Do something\n- [ ] Do something else\n")
            with patch.object(STAGES_MODULE, "workflow_dir", return_value=Path(tmp)):
                count, sha = MODULE.parse_checkpoint_markers("slug")
        self.assertEqual(count, 0)
        self.assertEqual(sha, "")

    def test_parse_checkpoint_markers_counts_all_list_styles(self) -> None:
        content = (
            "# Tasks\n"
            "- Task A [CHECKPOINT]\n"
            "* Task B [CHECKPOINT]\n"
            "1. Task C [CHECKPOINT]\n"
            "- [ ] Task D [CHECKPOINT]\n"
            "- [x] Task E [CHECKPOINT]\n"
            "- [X] Task F [CHECKPOINT]\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tasks = Path(tmp) / "tasks.md"
            tasks.write_text(content)
            with patch.object(STAGES_MODULE, "workflow_dir", return_value=Path(tmp)):
                count, sha = MODULE.parse_checkpoint_markers("slug")
        self.assertEqual(count, 6)
        self.assertNotEqual(sha, "")

    def test_parse_checkpoint_markers_ignores_non_list_lines(self) -> None:
        content = (
            "# Tasks\n"
            "This line mentions [CHECKPOINT] but is not a list item.\n"
            "    [CHECKPOINT] also not a list item\n"
            "- Real checkpoint [CHECKPOINT]\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tasks = Path(tmp) / "tasks.md"
            tasks.write_text(content)
            with patch.object(STAGES_MODULE, "workflow_dir", return_value=Path(tmp)):
                count, sha = MODULE.parse_checkpoint_markers("slug")
        self.assertEqual(count, 1)

    def test_parse_checkpoint_markers_anchors_to_end_of_line(self) -> None:
        # [CHECKPOINT] mid-sentence should not match
        content = "- [ ] Explain how [CHECKPOINT] marker works in detail\n"
        with tempfile.TemporaryDirectory() as tmp:
            tasks = Path(tmp) / "tasks.md"
            tasks.write_text(content)
            with patch.object(STAGES_MODULE, "workflow_dir", return_value=Path(tmp)):
                count, _ = MODULE.parse_checkpoint_markers("slug")
        self.assertEqual(count, 0)

    def test_parse_checkpoint_markers_returns_zero_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(STAGES_MODULE, "workflow_dir", return_value=Path(tmp)):
                count, sha = MODULE.parse_checkpoint_markers("slug")
        self.assertEqual(count, 0)
        self.assertEqual(sha, "")

    def test_checkpoint_progress_defaults_when_absent(self) -> None:
        with patch.object(STAGES_MODULE, "load_workflow_state", return_value={}):
            progress = MODULE.checkpoint_progress_state("slug")
        self.assertEqual(progress["index"], 0)
        self.assertEqual(progress["markers_sha"], "")
        self.assertEqual(progress["last_diff_head_sha"], "")

    def test_checkpoint_progress_normalizes_partial_state(self) -> None:
        partial = {MODULE.CHECKPOINT_PROGRESS_KEY: {"index": 2}}
        with patch.object(STAGES_MODULE, "load_workflow_state", return_value=partial):
            progress = MODULE.checkpoint_progress_state("slug")
        self.assertEqual(progress["index"], 2)
        self.assertEqual(progress["markers_sha"], "")
        self.assertEqual(progress["last_diff_head_sha"], "")

    def test_advance_checkpoint_progress_increments_index(self) -> None:
        initial = {"index": 1, "markers_sha": "old", "last_diff_head_sha": "oldsha"}
        captured: list[dict] = []

        def fake_update(slug: str, fn) -> None:
            import copy
            state = {MODULE.CHECKPOINT_PROGRESS_KEY: copy.deepcopy(initial)}
            fn(state)
            captured.append(state[MODULE.CHECKPOINT_PROGRESS_KEY])

        with (
            patch.object(REVIEW_MODULE, "load_workflow_state", return_value={MODULE.CHECKPOINT_PROGRESS_KEY: initial}),
            patch.object(REVIEW_MODULE, "parse_checkpoint_markers", return_value=(3, "newsha")),
            patch.object(REVIEW_MODULE, "checkpoint_progress_state", return_value=initial),
            patch.object(REVIEW_MODULE, "update_workflow_state", side_effect=fake_update),
        ):
            MODULE._advance_checkpoint_progress("slug", "diff", "newhead")

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["index"], 2)
        self.assertEqual(captured[0]["markers_sha"], "newsha")
        self.assertEqual(captured[0]["last_diff_head_sha"], "newhead")

    def test_advance_checkpoint_progress_no_op_for_non_diff(self) -> None:
        with patch.object(MODULE, "update_workflow_state") as mock_update:
            MODULE._advance_checkpoint_progress("slug", "spec", "sha")
        mock_update.assert_not_called()

    def test_advance_checkpoint_progress_no_op_when_no_markers(self) -> None:
        with (
            patch.object(MODULE, "parse_checkpoint_markers", return_value=(0, "")),
            patch.object(MODULE, "update_workflow_state") as mock_update,
        ):
            MODULE._advance_checkpoint_progress("slug", "diff", "sha")
        mock_update.assert_not_called()

    def test_sha_valid_ancestor_returns_false_for_empty(self) -> None:
        result = MODULE._sha_is_valid_ancestor("")
        self.assertFalse(result)

    def test_markers_sha_stable_across_non_marker_edits(self) -> None:
        """Adding a non-checkpoint task should not change the markers_sha."""
        base_content = "- Task A [CHECKPOINT]\n"
        extended_content = "- Task A [CHECKPOINT]\n- Task B no marker\n"
        with tempfile.TemporaryDirectory() as tmp:
            tasks = Path(tmp) / "tasks.md"
            tasks.write_text(base_content)
            with patch.object(MODULE, "workflow_dir", return_value=Path(tmp)):
                count1, sha1 = MODULE.parse_checkpoint_markers("slug")
            tasks.write_text(extended_content)
            with patch.object(MODULE, "workflow_dir", return_value=Path(tmp)):
                count2, sha2 = MODULE.parse_checkpoint_markers("slug")
        self.assertEqual(count1, count2)
        self.assertEqual(sha1, sha2)


class DefaultCodexModelTests(unittest.TestCase):
    def test_default_codex_model_constant_exists(self) -> None:
        self.assertEqual(MODULE.DEFAULT_CODEX_MODEL, "gpt-5.4-mini")

    def test_required_reviews_codex_entry_uses_constant(self) -> None:
        # Bundle 2: diff/tasks/closeout have no default codex review; plan still
        # carries a Codex lens, so test the model constant there.
        reviews = MODULE.required_reviews(
            "plan",
            sensitive=False,
            large_structural=False,
            performance_sensitive=False,
            extra_gemini=[],
        )
        codex_entries = [r for r in reviews if r.get("reviewer") == "codex"]
        self.assertTrue(codex_entries, "expected at least one codex reviewer entry")
        for entry in codex_entries:
            self.assertEqual(
                entry.get("model"),
                MODULE.DEFAULT_CODEX_MODEL,
                f"codex entry model should be DEFAULT_CODEX_MODEL, got {entry.get('model')!r}",
            )

    def test_required_reviews_tasks_small_task_set_has_no_default_reviews(self) -> None:
        # Bundle 2: the tasks stage has no default reviews regardless of size.
        reviews = MODULE.required_reviews(
            "tasks",
            sensitive=False,
            large_structural=False,
            performance_sensitive=False,
            extra_gemini=[],
            small_task_set=True,
        )
        self.assertEqual(reviews, [])

    def test_required_reviews_tasks_large_task_set_has_no_default_reviews(self) -> None:
        # Bundle 2: tasks has no default Codex or Gemini review.
        reviews = MODULE.required_reviews(
            "tasks",
            sensitive=False,
            large_structural=False,
            performance_sensitive=False,
            extra_gemini=[],
            small_task_set=False,
        )
        self.assertEqual(reviews, [])

    def test_required_reviews_tasks_small_task_set_sensitive_still_skips_gemini(self) -> None:
        # sensitive flag adds risk-adversarial as an extra Gemini candidate, but
        # small_task_set still wins and skips all Gemini reviews
        reviews = MODULE.required_reviews(
            "tasks",
            sensitive=True,
            large_structural=False,
            performance_sensitive=False,
            extra_gemini=[],
            small_task_set=True,
        )
        gemini_entries = [r for r in reviews if r.get("reviewer") == "gemini"]
        self.assertEqual(gemini_entries, [], "small_task_set should skip Gemini even when sensitive=True")

    def test_required_reviews_closeout_small_task_set_has_no_default_reviews(self) -> None:
        # Bundle 2: the closeout stage has no default reviews regardless of size.
        reviews = MODULE.required_reviews(
            "closeout",
            sensitive=False,
            large_structural=False,
            performance_sensitive=False,
            extra_gemini=[],
            small_task_set=True,
        )
        self.assertEqual(reviews, [])

    def test_required_reviews_closeout_large_task_set_has_no_default_reviews(self) -> None:
        # Bundle 2: closeout has no default Codex or Gemini review.
        reviews = MODULE.required_reviews(
            "closeout",
            sensitive=False,
            large_structural=False,
            performance_sensitive=False,
            extra_gemini=[],
            small_task_set=False,
        )
        self.assertEqual(reviews, [])


class _CapturedBaseRef(Exception):
    """Sentinel exception raised by the preferred_diff_base_ref mock to abort command_checkpoint early."""

    def __init__(self, base_ref: object) -> None:
        self.base_ref = base_ref


class BaseRefResetTests(unittest.TestCase):
    """Tests that base_ref is reset to None in all three reset branches of command_checkpoint."""

    def _run_checkpoint_diff_get_base_ref(
        self,
        *,
        marker_count: int,
        index: int,
        stored_sha: str,
        last_head: str,
        ancestor_valid: bool,
        recorded_base_ref: str = "origin/main",
    ) -> object:
        """Run command_checkpoint diff stage and return the base_ref passed to preferred_diff_base_ref.

        Mocks preferred_diff_base_ref to raise _CapturedBaseRef so we can inspect what arg it received
        without needing to mock all of command_checkpoint's downstream subprocess calls.
        """

        def capturing_preferred(slug: str, base_ref: object) -> str:
            raise _CapturedBaseRef(base_ref)

        progress = {"index": index, "markers_sha": stored_sha, "last_diff_head_sha": last_head}

        args = SimpleNamespace(
            slug="test-slug",
            stage="diff",
            path=[],
            artifact="",
            base_ref=None,
            context=[],
            allow_dirty_path=[],
            max_artifact_chars=None,
            max_context_chars=None,
            max_total_chars=None,
            gemini_timeout_seconds=120,
            gemini_retries=1,
            repair_timeout_seconds=30,
            fallback=False,
            use_existing_artifact=False,
            allow_large_diff_rerun=False,
            required_reviews=None,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            workflow_root = temp_root / "workflow"
            reviews_root = workflow_root / "reviews"
            reviews_root.mkdir(parents=True, exist_ok=True)

            try:
                with (
                    patch.object(CMD_CHECKPOINT_MODULE, "ensure_sync"),
                    patch.object(CMD_CHECKPOINT_MODULE, "workflow_dir", return_value=workflow_root),
                    patch.object(CMD_CHECKPOINT_MODULE, "reviews_dir", return_value=reviews_root),
                    patch.object(CMD_CHECKPOINT_MODULE, "prerequisite_failure", return_value=None),
                    patch.object(CMD_CHECKPOINT_MODULE, "resolved_review_policy", return_value={
                        "sensitive": False,
                        "large_structural": False,
                        "performance_sensitive": False,
                        "extra_gemini_lenses": [],
                    }),
                    patch.object(CMD_CHECKPOINT_MODULE, "diff_review_budget_state", return_value={
                        "artifact_exists": False,
                        "artifact_bytes": 0,
                        "large_artifact": False,
                        "recorded_base_ref": recorded_base_ref,
                        "recorded_base_sha": "aabbccddee",
                        "recorded_head_sha": "deadbeef111",
                        "current_head_sha": "newheadsha",
                        "head_mismatch": True,
                        "scope_basis": "last-reviewed-head",
                        "suggested_base_ref": "deadbeef111",
                        "artifact_changed_since_codex": False,
                    }),
                    patch.object(CMD_CHECKPOINT_MODULE, "checkpoint_progress_state", return_value=progress),
                    patch.object(CMD_CHECKPOINT_MODULE, "parse_checkpoint_markers", return_value=(marker_count, "CURRENT_SHA")),
                    patch.object(CMD_CHECKPOINT_MODULE, "_sha_is_valid_ancestor", return_value=ancestor_valid),
                    patch.object(CMD_CHECKPOINT_MODULE, "update_workflow_state"),
                    patch.object(CMD_CHECKPOINT_MODULE, "preferred_diff_base_ref", side_effect=capturing_preferred),
                    redirect_stdout(io.StringIO()),
                ):
                    MODULE.command_checkpoint(args)
            except _CapturedBaseRef as e:
                return e.base_ref
            except (SystemExit, Exception):
                pass

        return None

    def test_index_overflow_clears_base_ref(self) -> None:
        """index >= marker_count triggers reset; preferred_diff_base_ref must receive None."""
        base_ref = self._run_checkpoint_diff_get_base_ref(
            marker_count=1,
            index=2,
            stored_sha="SHA1",
            last_head="abc123",
            ancestor_valid=True,
        )
        self.assertIsNone(base_ref, "expected preferred_diff_base_ref to be called with None after index overflow reset")

    def test_markers_sha_mismatch_clears_base_ref(self) -> None:
        """stored_sha != current_sha triggers reset; preferred_diff_base_ref must receive None."""
        base_ref = self._run_checkpoint_diff_get_base_ref(
            marker_count=1,
            index=1,
            stored_sha="OLD_SHA",
            last_head="abc123",
            ancestor_valid=True,
        )
        self.assertIsNone(base_ref, "expected preferred_diff_base_ref to be called with None after markers-sha mismatch reset")

    def test_dangling_sha_clears_base_ref(self) -> None:
        """last_head not a valid ancestor triggers reset; preferred_diff_base_ref must receive None."""
        base_ref = self._run_checkpoint_diff_get_base_ref(
            marker_count=1,
            index=1,
            stored_sha="CURRENT_SHA",
            last_head="dangling",
            ancestor_valid=False,
        )
        self.assertIsNone(base_ref, "expected preferred_diff_base_ref to be called with None after dangling SHA reset")

    def test_reset_uses_recorded_base_not_stale_head(self) -> None:
        """After an index-overflow reset, preferred_diff_base_ref is called with None (not the stale last_head SHA).

        This test verifies that:
        (a) The reset fires (confirmed by update_workflow_state being called).
        (b) preferred_diff_base_ref receives None — the stale last_head SHA "deadbeef111" is NOT passed
            as the requested base_ref, so it cannot pollute the diff base selection.
        (c) args.base_ref is set to whatever preferred_diff_base_ref returns ("origin/main" in this mock).
        """
        args_holder: list[object] = []
        received_base_ref: list[object] = []

        def fake_preferred(slug: str, base_ref: object) -> str:
            received_base_ref.append(base_ref)
            return "origin/main"

        progress = {"index": 2, "markers_sha": "SHA1", "last_diff_head_sha": "deadbeef111"}

        args = SimpleNamespace(
            slug="test-slug",
            stage="diff",
            path=[],
            artifact="",
            base_ref=None,
            context=[],
            allow_dirty_path=[],
            max_artifact_chars=None,
            max_context_chars=None,
            max_total_chars=None,
            gemini_timeout_seconds=120,
            gemini_retries=1,
            repair_timeout_seconds=30,
            fallback=False,
            use_existing_artifact=False,
            allow_large_diff_rerun=False,
            required_reviews=None,
        )
        args_holder.append(args)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            workflow_root = temp_root / "workflow"
            reviews_root = workflow_root / "reviews"
            reviews_root.mkdir(parents=True, exist_ok=True)

            def capturing_scope(slug: str) -> Path:
                # Capture args.base_ref at the point after preferred_diff_base_ref has set it.
                raise _CapturedBaseRef(args_holder[0].base_ref)

            try:
                with (
                    patch.object(CMD_CHECKPOINT_MODULE, "ensure_sync"),
                    patch.object(CMD_CHECKPOINT_MODULE, "workflow_dir", return_value=workflow_root),
                    patch.object(CMD_CHECKPOINT_MODULE, "reviews_dir", return_value=reviews_root),
                    patch.object(CMD_CHECKPOINT_MODULE, "prerequisite_failure", return_value=None),
                    patch.object(CMD_CHECKPOINT_MODULE, "resolved_review_policy", return_value={
                        "sensitive": False,
                        "large_structural": False,
                        "performance_sensitive": False,
                        "extra_gemini_lenses": [],
                    }),
                    patch.object(CMD_CHECKPOINT_MODULE, "diff_review_budget_state", return_value={
                        "artifact_exists": False,
                        "artifact_bytes": 0,
                        "large_artifact": False,
                        "recorded_base_ref": "origin/main",
                        "recorded_base_sha": "aabbccddee",
                        "recorded_head_sha": "deadbeef111",
                        "current_head_sha": "newheadsha",
                        "head_mismatch": True,
                        "scope_basis": "last-reviewed-head",
                        "suggested_base_ref": "deadbeef111",
                        "artifact_changed_since_codex": False,
                    }),
                    patch.object(CMD_CHECKPOINT_MODULE, "checkpoint_progress_state", return_value=progress),
                    patch.object(CMD_CHECKPOINT_MODULE, "parse_checkpoint_markers", return_value=(1, "CURRENT_SHA")),
                    patch.object(CMD_CHECKPOINT_MODULE, "_sha_is_valid_ancestor", return_value=True),
                    patch.object(CMD_CHECKPOINT_MODULE, "update_workflow_state"),
                    patch.object(CMD_CHECKPOINT_MODULE, "preferred_diff_base_ref", side_effect=fake_preferred),
                    patch.object(CMD_CHECKPOINT_MODULE, "scope_manifest_path", side_effect=capturing_scope),
                    redirect_stdout(io.StringIO()),
                ):
                    MODULE.command_checkpoint(args)
            except _CapturedBaseRef as e:
                captured_result = e.base_ref
            except (SystemExit, Exception):
                captured_result = None
            else:
                captured_result = None

        # preferred_diff_base_ref should receive None (args.base_ref was reset, not the stale last_head)
        self.assertEqual(len(received_base_ref), 1, "expected preferred_diff_base_ref to be called once")
        self.assertIsNone(received_base_ref[0], "expected preferred_diff_base_ref to be called with None, not the stale 'deadbeef111'")
        # The resulting args.base_ref should be what preferred_diff_base_ref returned
        self.assertEqual(captured_result, "origin/main", "expected args.base_ref to be set to what preferred_diff_base_ref returned")


class RepairCloseoutTests(unittest.TestCase):
    """Tests for the closeout repair block added to command_repair."""

    def _base_stages(self) -> dict:
        healthy = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True)
        return {s: healthy for s in ["spec", "plan", "tasks", "diff", "closeout"]}

    def test_repair_skips_closeout_when_not_checkpointed(self) -> None:
        """not-checkpointed closeout: repair succeeds, no block, command_checkpoint not called for closeout."""
        checkpoint_call_count: list[int] = [0]

        def counting_checkpoint(repair_args: object) -> int:
            checkpoint_call_count[0] += 1
            return 0

        stages = self._base_stages()
        stages["closeout"] = stage_state()  # not-checkpointed

        args = MODULE.argparse.Namespace(slug="test-slug")

        with (
            patch.object(CMD_STATUS_MODULE, "ensure_sync"),
            patch.object(CMD_STATUS_MODULE, "load_workflow_state", return_value={"blocked": {"active": False}, "delivery": {}}),
            patch.object(CMD_STATUS_MODULE, "stage_manifest_state", side_effect=lambda slug, stage: stages[stage]),
            patch.object(CMD_STATUS_MODULE, "stage_drift_class", side_effect=lambda stage, state: "not-checkpointed" if stage == "closeout" else "healthy"),
            patch.object(CMD_STATUS_MODULE, "stage_repairable", return_value=False),
            patch.object(CMD_STATUS_MODULE, "stage_review_inventory", return_value=([], [])),
            patch.object(CMD_CHECKPOINT_MODULE, "command_checkpoint", side_effect=counting_checkpoint),
            patch.object(CMD_STATUS_MODULE, "reconciliation_state", return_value=(True, "")),
            patch.object(CMD_STATUS_MODULE, "recommended_next_action", return_value="closeout"),
            patch.object(CMD_STATUS_MODULE, "stage_status_label", return_value="not-checkpointed"),
            patch.object(CMD_STATUS_MODULE, "trim_detail", side_effect=lambda s: s),
            redirect_stdout(io.StringIO()),
        ):
            result = MODULE.command_repair(args)

        self.assertEqual(result, 0, "expected repair to succeed when closeout is not-checkpointed")
        self.assertEqual(checkpoint_call_count[0], 0, "command_checkpoint should not be called for not-checkpointed closeout")

    def test_repair_fixes_stale_closeout(self) -> None:
        """unhealthy-manifest + repairable: command_checkpoint called, closeout appears in repaired output."""
        checkpoint_calls: list[object] = []
        healthy = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True)
        closeout_initial = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=False)
        closeout_refreshed = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True)

        call_count: list[int] = [0]

        def fake_manifest_state(slug: str, stage: str) -> dict:
            if stage == "closeout":
                call_count[0] += 1
                return closeout_initial if call_count[0] == 1 else closeout_refreshed
            return healthy

        args = MODULE.argparse.Namespace(slug="test-slug")
        output = io.StringIO()

        with (
            patch.object(CMD_STATUS_MODULE, "ensure_sync"),
            patch.object(CMD_STATUS_MODULE, "load_workflow_state", return_value={"blocked": {"active": False}, "delivery": {}}),
            patch.object(CMD_STATUS_MODULE, "stage_manifest_state", side_effect=fake_manifest_state),
            patch.object(CMD_STATUS_MODULE, "stage_drift_class", side_effect=lambda stage, state: "unhealthy-manifest" if stage == "closeout" else "healthy"),
            patch.object(CMD_STATUS_MODULE, "stage_repairable", side_effect=lambda slug, stage, state: stage == "closeout"),
            patch.object(CMD_STATUS_MODULE, "stage_review_inventory", return_value=([], [])),
            patch.object(CMD_CHECKPOINT_MODULE, "command_checkpoint", side_effect=lambda a: checkpoint_calls.append(a) or 0),
            patch.object(CMD_STATUS_MODULE, "reconciliation_state", return_value=(True, "")),
            patch.object(CMD_STATUS_MODULE, "recommended_next_action", return_value="deliver"),
            patch.object(CMD_STATUS_MODULE, "stage_status_label", return_value="ok"),
            patch.object(CMD_STATUS_MODULE, "trim_detail", side_effect=lambda s: s),
            redirect_stdout(output),
        ):
            result = MODULE.command_repair(args)

        self.assertEqual(result, 0, "expected repair to succeed")
        self.assertEqual(len(checkpoint_calls), 1, "expected exactly one command_checkpoint call for closeout")
        self.assertIn("closeout", output.getvalue(), "expected closeout to appear in output")

    def test_repair_blocks_on_closeout_failure(self) -> None:
        """unhealthy-manifest + repairable but checkpoint returns 1: repair must return 1."""
        healthy = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True)
        closeout_initial = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=False)

        args = MODULE.argparse.Namespace(slug="test-slug")

        with (
            patch.object(CMD_STATUS_MODULE, "ensure_sync"),
            patch.object(CMD_STATUS_MODULE, "load_workflow_state", return_value={"blocked": {"active": False}, "delivery": {}}),
            patch.object(CMD_STATUS_MODULE, "stage_manifest_state", side_effect=lambda slug, stage: closeout_initial if stage == "closeout" else healthy),
            patch.object(CMD_STATUS_MODULE, "stage_drift_class", side_effect=lambda stage, state: "unhealthy-manifest" if stage == "closeout" else "healthy"),
            patch.object(CMD_STATUS_MODULE, "stage_repairable", side_effect=lambda slug, stage, state: stage == "closeout"),
            patch.object(CMD_STATUS_MODULE, "stage_review_inventory", return_value=([], [])),
            patch.object(CMD_CHECKPOINT_MODULE, "command_checkpoint", return_value=1),
            patch.object(CMD_STATUS_MODULE, "reconciliation_state", return_value=(True, "")),
            patch.object(CMD_STATUS_MODULE, "recommended_next_action", return_value="deliver"),
            patch.object(CMD_STATUS_MODULE, "stage_status_label", return_value="ok"),
            patch.object(CMD_STATUS_MODULE, "trim_detail", side_effect=lambda s: s),
            redirect_stdout(io.StringIO()),
        ):
            result = MODULE.command_repair(args)

        self.assertEqual(result, 1, "expected repair to fail when closeout checkpoint returns non-zero")

    def test_repair_blocks_when_closeout_unhealthy_not_repairable(self) -> None:
        """unhealthy-manifest but not repairable: repair must return 1 without calling command_checkpoint."""
        checkpoint_calls: list[object] = []
        healthy = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True)
        closeout_initial = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=False)

        args = MODULE.argparse.Namespace(slug="test-slug")

        with (
            patch.object(CMD_STATUS_MODULE, "ensure_sync"),
            patch.object(CMD_STATUS_MODULE, "load_workflow_state", return_value={"blocked": {"active": False}, "delivery": {}}),
            patch.object(CMD_STATUS_MODULE, "stage_manifest_state", side_effect=lambda slug, stage: closeout_initial if stage == "closeout" else healthy),
            patch.object(CMD_STATUS_MODULE, "stage_drift_class", side_effect=lambda stage, state: "unhealthy-manifest" if stage == "closeout" else "healthy"),
            patch.object(CMD_STATUS_MODULE, "stage_repairable", return_value=False),
            patch.object(CMD_STATUS_MODULE, "stage_review_inventory", return_value=([], [])),
            patch.object(CMD_CHECKPOINT_MODULE, "command_checkpoint", side_effect=lambda a: checkpoint_calls.append(a) or 0),
            patch.object(CMD_STATUS_MODULE, "reconciliation_state", return_value=(True, "")),
            patch.object(CMD_STATUS_MODULE, "recommended_next_action", return_value="deliver"),
            patch.object(CMD_STATUS_MODULE, "stage_status_label", return_value="ok"),
            patch.object(CMD_STATUS_MODULE, "trim_detail", side_effect=lambda s: s),
            redirect_stdout(io.StringIO()),
        ):
            result = MODULE.command_repair(args)

        self.assertEqual(result, 1, "expected repair to fail when closeout is unhealthy but not repairable")
        self.assertEqual(len(checkpoint_calls), 0, "command_checkpoint should not be called when closeout is not repairable")

    def test_repair_blocks_when_checkpoint_succeeds_but_closeout_remains_unhealthy(self) -> None:
        """command_checkpoint returns 0 but refreshed closeout is still unhealthy: repair must return 1."""
        healthy = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True)
        closeout_initial = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=False)
        closeout_still_unhealthy = stage_state(
            artifact_exists=True,
            artifact_meaningful=True,
            manifest_exists=True,
            healthy=False,
            detail="still broken after repair",
        )
        call_count: list[int] = [0]

        def fake_manifest_state(slug: str, stage: str) -> dict:
            if stage == "closeout":
                call_count[0] += 1
                # First call: initial state for the repair loop.
                # Second call: after checkpoint, refreshed state still unhealthy.
                return closeout_initial if call_count[0] == 1 else closeout_still_unhealthy
            return healthy

        args = MODULE.argparse.Namespace(slug="test-slug")

        with (
            patch.object(CMD_STATUS_MODULE, "ensure_sync"),
            patch.object(CMD_STATUS_MODULE, "load_workflow_state", return_value={"blocked": {"active": False}, "delivery": {}}),
            patch.object(CMD_STATUS_MODULE, "stage_manifest_state", side_effect=fake_manifest_state),
            patch.object(CMD_STATUS_MODULE, "stage_drift_class", side_effect=lambda stage, state: "unhealthy-manifest" if stage == "closeout" else "healthy"),
            patch.object(CMD_STATUS_MODULE, "stage_repairable", side_effect=lambda slug, stage, state: stage == "closeout"),
            patch.object(CMD_STATUS_MODULE, "stage_review_inventory", return_value=([], [])),
            patch.object(CMD_CHECKPOINT_MODULE, "command_checkpoint", return_value=0),
            patch.object(CMD_STATUS_MODULE, "reconciliation_state", return_value=(True, "")),
            patch.object(CMD_STATUS_MODULE, "recommended_next_action", return_value="deliver"),
            patch.object(CMD_STATUS_MODULE, "stage_status_label", return_value="ok"),
            patch.object(CMD_STATUS_MODULE, "trim_detail", side_effect=lambda s: s),
            redirect_stdout(io.StringIO()),
        ):
            result = MODULE.command_repair(args)

        self.assertEqual(result, 1, "expected repair to fail when closeout remains unhealthy even after checkpoint returns 0")

    def test_repair_skips_closeout_when_earlier_stage_blocked(self) -> None:
        """When an earlier stage (diff) fails and sets blocked_reason, the closeout repair must not run.

        This exercises the `if not blocked_reason:` guard that wraps the closeout block.
        """
        checkpoint_calls: list[object] = []
        healthy = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=True)

        # diff is unhealthy + not repairable → will set blocked_reason during the main repair loop
        diff_unhealthy = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=False)
        closeout_unhealthy = stage_state(artifact_exists=True, artifact_meaningful=True, manifest_exists=True, healthy=False)

        def fake_manifest_state(slug: str, stage: str) -> dict:
            if stage == "diff":
                return diff_unhealthy
            if stage == "closeout":
                return closeout_unhealthy
            return healthy

        def fake_drift(stage: str, state: object) -> str:
            if stage in {"diff", "closeout"}:
                return "unhealthy-manifest"
            return "healthy"

        def fake_repairable(slug: str, stage: str, state: object) -> bool:
            return False  # neither diff nor closeout is repairable

        args = MODULE.argparse.Namespace(slug="test-slug")

        with (
            patch.object(CMD_STATUS_MODULE, "ensure_sync"),
            patch.object(CMD_STATUS_MODULE, "load_workflow_state", return_value={"blocked": {"active": False}, "delivery": {}}),
            patch.object(CMD_STATUS_MODULE, "stage_manifest_state", side_effect=fake_manifest_state),
            patch.object(CMD_STATUS_MODULE, "stage_drift_class", side_effect=fake_drift),
            patch.object(CMD_STATUS_MODULE, "stage_repairable", side_effect=fake_repairable),
            patch.object(CMD_STATUS_MODULE, "stage_review_inventory", return_value=([], [])),
            patch.object(CMD_STATUS_MODULE, "later_progress_exists", return_value=(False, "")),
            patch.object(CMD_CHECKPOINT_MODULE, "command_checkpoint", side_effect=lambda a: checkpoint_calls.append(a) or 0),
            patch.object(CMD_STATUS_MODULE, "reconciliation_state", return_value=(True, "")),
            patch.object(CMD_STATUS_MODULE, "recommended_next_action", return_value="deliver"),
            patch.object(CMD_STATUS_MODULE, "stage_status_label", return_value="ok"),
            patch.object(CMD_STATUS_MODULE, "trim_detail", side_effect=lambda s: s),
            redirect_stdout(io.StringIO()),
        ):
            result = MODULE.command_repair(args)

        self.assertEqual(result, 1, "expected repair to fail due to diff being blocked")
        self.assertEqual(len(checkpoint_calls), 0, "command_checkpoint should not be called for closeout when earlier stage is blocked")


if __name__ == "__main__":
    unittest.main()


class TestMigrateDiscoveryState(unittest.TestCase):
    """Wave 1: Tests for default_discovery_state() V2 schema and migrate_discovery_state()."""

    def test_default_discovery_state_has_v2_fields(self) -> None:
        d = FACTORY_STATE.default_discovery_state()
        self.assertEqual(d["version"], 2)
        self.assertIn("answers", d)
        self.assertIn("non_goals", d)
        self.assertIn("acceptance_criteria", d)
        self.assertIn("unresolved", d)
        self.assertIsInstance(d["answers"], dict)
        self.assertIsInstance(d["non_goals"], list)
        self.assertIsInstance(d["acceptance_criteria"], list)
        self.assertIsInstance(d["unresolved"], list)

    def test_migrate_noop_on_v2(self) -> None:
        v2 = {"version": 2, "answers": {"q": "a"}, "non_goals": ["x"], "unresolved": []}
        result = FACTORY_STATE.migrate_discovery_state(v2)
        self.assertIs(result, v2)  # same object returned unchanged

    def test_migrate_sets_version_2(self) -> None:
        v1 = {"version": 1, "required": False, "complete": True, "questions": [], "assumptions": []}
        result = FACTORY_STATE.migrate_discovery_state(v1)
        self.assertEqual(result["version"], 2)

    def test_migrate_adds_missing_v2_fields(self) -> None:
        v1 = {"version": 1, "required": False, "complete": True}
        result = FACTORY_STATE.migrate_discovery_state(v1)
        self.assertIn("answers", result)
        self.assertIn("non_goals", result)
        self.assertIn("acceptance_criteria", result)
        self.assertIn("unresolved", result)

    def test_migrate_does_not_mutate_input(self) -> None:
        v1 = {"version": 1, "required": False, "complete": True}
        original_version = v1["version"]
        FACTORY_STATE.migrate_discovery_state(v1)
        self.assertEqual(v1["version"], original_version)  # input unchanged

    def test_migrate_preserves_existing_data(self) -> None:
        v1 = {
            "version": 1,
            "required": True,
            "complete": True,
            "questions": [{"question": "Q1", "recommendation": "r", "rationale": "r", "updated_at": 0}],
            "assumptions": ["Assumption A"],
            "summary": "Summary text",
        }
        result = FACTORY_STATE.migrate_discovery_state(v1)
        self.assertEqual(result["questions"], v1["questions"])
        self.assertEqual(result["assumptions"], v1["assumptions"])
        self.assertEqual(result["summary"], v1["summary"])
        self.assertTrue(result["required"])
        self.assertTrue(result["complete"])

    def test_migrate_populates_unresolved_from_questions_when_required_incomplete(self) -> None:
        v1 = {
            "version": 1,
            "required": True,
            "complete": False,
            "questions": [
                {"question": "Q1", "recommendation": "r1", "rationale": "r1", "updated_at": 0},
                {"question": "Q2", "recommendation": "r2", "rationale": "r2", "updated_at": 0},
            ],
            "assumptions": [],
        }
        result = FACTORY_STATE.migrate_discovery_state(v1)
        items = [u["item"] for u in result["unresolved"]]
        self.assertIn("Q1", items)
        self.assertIn("Q2", items)
        for u in result["unresolved"]:
            self.assertFalse(u["deferred"])

    def test_migrate_does_not_populate_unresolved_when_complete(self) -> None:
        v1 = {
            "version": 1,
            "required": True,
            "complete": True,
            "questions": [
                {"question": "Q1", "recommendation": "r", "rationale": "r", "updated_at": 0},
            ],
            "assumptions": [],
        }
        result = FACTORY_STATE.migrate_discovery_state(v1)
        self.assertEqual(result["unresolved"], [])

    def test_migrate_does_not_populate_unresolved_when_not_required(self) -> None:
        v1 = {
            "version": 1,
            "required": False,
            "complete": False,
            "questions": [
                {"question": "Q1", "recommendation": "r", "rationale": "r", "updated_at": 0},
            ],
            "assumptions": [],
        }
        result = FACTORY_STATE.migrate_discovery_state(v1)
        self.assertEqual(result["unresolved"], [])

    def test_migrate_handles_malformed_questions_list(self) -> None:
        v1 = {"version": 1, "required": True, "complete": False, "questions": None}
        result = FACTORY_STATE.migrate_discovery_state(v1)  # must not raise
        self.assertEqual(result["unresolved"], [])

    def test_migrate_sanitizes_malformed_unresolved_entries(self) -> None:
        v1 = {
            "version": 1,
            "required": False,
            "complete": True,
            "unresolved": ["string", 42, {"item": "valid", "deferred": False}, {"no_item_key": "x"}],
        }
        result = FACTORY_STATE.migrate_discovery_state(v1)
        self.assertEqual(len(result["unresolved"]), 1)
        self.assertEqual(result["unresolved"][0]["item"], "valid")

    def test_migrate_deduplicates_questions_in_unresolved(self) -> None:
        v1 = {
            "version": 1,
            "required": True,
            "complete": False,
            "questions": [
                {"question": "Q1", "recommendation": "r", "rationale": "r", "updated_at": 0},
                {"question": "Q1", "recommendation": "r", "rationale": "r", "updated_at": 0},
            ],
            "assumptions": [],
        }
        result = FACTORY_STATE.migrate_discovery_state(v1)
        self.assertEqual(len(result["unresolved"]), 1)
        self.assertEqual(result["unresolved"][0]["item"], "Q1")

    def test_migrate_skips_questions_with_null_text(self) -> None:
        v1 = {
            "version": 1,
            "required": True,
            "complete": False,
            "questions": [
                {"question": None, "recommendation": "r", "rationale": "r", "updated_at": 0},
                {"question": "", "recommendation": "r", "rationale": "r", "updated_at": 0},
                {"question": "  ", "recommendation": "r", "rationale": "r", "updated_at": 0},
            ],
            "assumptions": [],
        }
        result = FACTORY_STATE.migrate_discovery_state(v1)
        self.assertEqual(result["unresolved"], [])


class TestParsePAnnotation(unittest.TestCase):
    def test_valid_single_file(self) -> None:
        line = "- [ ] T001 [P: src/foo.ts] do thing"
        self.assertEqual(STAGES_MODULE.parse_p_annotation(line), ["src/foo.ts"])

    def test_valid_multiple_files(self) -> None:
        line = "- [ ] T001 [P: src/foo.ts, src/bar.ts] do thing"
        self.assertEqual(STAGES_MODULE.parse_p_annotation(line), ["src/foo.ts", "src/bar.ts"])

    def test_empty_list(self) -> None:
        line = "- [ ] T001 [P:] do thing"
        self.assertEqual(STAGES_MODULE.parse_p_annotation(line), [])

    def test_bare_p_no_colon(self) -> None:
        line = "- [ ] T001 [P] do thing"
        self.assertEqual(STAGES_MODULE.parse_p_annotation(line), [])

    def test_no_annotation(self) -> None:
        line = "- [ ] T001 do thing"
        self.assertEqual(STAGES_MODULE.parse_p_annotation(line), [])

    def test_dot_slash_prefix_normalized(self) -> None:
        line = "- [ ] T001 [P: ./src/foo.ts] do thing"
        self.assertEqual(STAGES_MODULE.parse_p_annotation(line), ["src/foo.ts"])

    def test_duplicate_paths_deduped(self) -> None:
        line = "- [ ] T001 [P: src/foo.ts, src/foo.ts] do thing"
        self.assertEqual(STAGES_MODULE.parse_p_annotation(line), ["src/foo.ts"])

    def test_absolute_path_rejected(self) -> None:
        line = "- [ ] T001 [P: /etc/passwd] do thing"
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = STAGES_MODULE.parse_p_annotation(line)
        self.assertEqual(result, [])
        self.assertIn("rejecting absolute path", stderr.getvalue())

    def test_dotdot_escape_rejected(self) -> None:
        line = "- [ ] T001 [P: ../outside/repo.ts] do thing"
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = STAGES_MODULE.parse_p_annotation(line)
        self.assertEqual(result, [])
        self.assertIn("rejecting path outside repository", stderr.getvalue())

    def test_paths_with_subdirs(self) -> None:
        line = "- [ ] T001 [P: src/services/foo.ts, cloud/apps/api/src/index.ts] do thing"
        self.assertEqual(
            STAGES_MODULE.parse_p_annotation(line),
            ["src/services/foo.ts", "cloud/apps/api/src/index.ts"],
        )


class TestParseParallelTaskGroups(unittest.TestCase):
    def _groups(self, tasks_md: str) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tasks.md").write_text(tasks_md, encoding="utf-8")
            with patch.object(STAGES_MODULE, "workflow_dir", return_value=root):
                return STAGES_MODULE.parse_parallel_task_groups("slug")

    def test_no_p_tasks_returns_serial_group(self) -> None:
        groups = self._groups(
            "- [ ] T001 do thing\n"
            "- [ ] T002 do other\n"
        )
        self.assertEqual(len(groups), 1)
        self.assertFalse(groups[0]["parallel"])
        self.assertIsNone(groups[0]["overlap_warning"])
        self.assertEqual(groups[0]["files"], [])
        self.assertEqual(len(groups[0]["tasks"]), 2)

    def test_two_nonoverlapping_p_tasks_returns_parallel_group(self) -> None:
        groups = self._groups(
            "- [ ] T001 [P: src/a.ts] do thing\n"
            "- [ ] T002 [P: src/b.ts] do thing\n"
        )
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0]["parallel"])
        self.assertIsNone(groups[0]["overlap_warning"])
        self.assertEqual(groups[0]["files"], ["src/a.ts", "src/b.ts"])
        self.assertEqual(len(groups[0]["tasks"]), 2)

    def test_two_overlapping_p_tasks_returns_serial_with_warning(self) -> None:
        groups = self._groups(
            "- [ ] T001 [P: src/same-file.ts] do thing\n"
            "- [ ] T002 [P: src/same-file.ts] do thing\n"
        )
        self.assertEqual(len(groups), 1)
        self.assertFalse(groups[0]["parallel"])
        self.assertIn("same-file.ts", groups[0]["overlap_warning"])
        self.assertEqual(len(groups[0]["tasks"]), 2)

    def test_single_p_task_returns_serial(self) -> None:
        groups = self._groups("- [ ] T001 [P: src/file.ts] do thing\n")
        self.assertEqual(len(groups), 1)
        self.assertFalse(groups[0]["parallel"])
        self.assertIsNone(groups[0]["overlap_warning"])
        self.assertEqual(len(groups[0]["tasks"]), 1)

    def test_bare_p_without_file_list_treated_as_unannotated(self) -> None:
        groups = self._groups(
            "- [ ] T001 [P] do thing\n"
            "- [ ] T002 [P: src/file.ts] do thing\n"
        )
        self.assertEqual(len(groups), 1)
        self.assertFalse(groups[0]["parallel"])
        self.assertIsNone(groups[0]["overlap_warning"])
        self.assertEqual(len(groups[0]["tasks"]), 2)

    def test_tasks_after_checkpoint_not_included(self) -> None:
        groups = self._groups(
            "- [ ] T001 do thing\n"
            "- [ ] T002 [CHECKPOINT]\n"
            "- [ ] T003 do later\n"
        )
        self.assertEqual(len(groups), 1)
        self.assertFalse(groups[0]["parallel"])
        self.assertEqual(len(groups[0]["tasks"]), 1)

    def test_all_tasks_checked_returns_empty(self) -> None:
        groups = self._groups(
            "- [x] T001 do thing\n"
            "- [x] T002 do other\n"
        )
        self.assertEqual(groups, [])

    def test_dot_slash_and_bare_path_overlap_detected(self) -> None:
        groups = self._groups(
            "- [ ] T001 [P: ./src/a.ts] do thing\n"
            "- [ ] T002 [P: src/a.ts] do thing\n"
        )
        self.assertEqual(len(groups), 1)
        self.assertFalse(groups[0]["parallel"])
        self.assertIn("src/a.ts", groups[0]["overlap_warning"])


class TestWorktreeHelpers(unittest.TestCase):
    def test_create_worktree_calls_correct_git_commands(self) -> None:
        calls: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with patch.object(Path, "exists", return_value=False):
            path = FACTORY_GIT.create_worktree("my-slug", 0, run_fn=mock_run)

        self.assertIn("my-slug", str(path))
        self.assertEqual(
            calls[-1],
            ["git", "-C", str(FACTORY_GIT.REPO_ROOT), "worktree", "add", str(path), "HEAD"],
        )

    def test_create_worktree_removes_stale_before_add(self) -> None:
        calls: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with patch.object(Path, "exists", return_value=True):
            path = FACTORY_GIT.create_worktree("my-slug", 1, run_fn=mock_run)

        remove_index = next(
            i for i, cmd in enumerate(calls) if cmd[3:6] == ["worktree", "remove", "--force"]
        )
        add_index = next(i for i, cmd in enumerate(calls) if cmd[3:5] == ["worktree", "add"])
        self.assertLess(remove_index, add_index)
        self.assertIn(str(path), calls[remove_index])
        self.assertTrue(any(cmd == ["git", "-C", str(FACTORY_GIT.REPO_ROOT), "worktree", "prune"] for cmd in calls))

    def test_remove_worktree_silent_if_path_absent(self) -> None:
        calls: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with patch.object(Path, "exists", return_value=False):
            FACTORY_GIT.remove_worktree(Path("/tmp/nonexistent_12345"), run_fn=mock_run)

        self.assertEqual(calls, [])

    def test_stage_and_commit_if_dirty_returns_sha_when_dirty(self) -> None:
        calls: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if cmd[-2:] == ["status", "--porcelain"]:
                result.stdout = "M foo.ts\n"
            elif cmd[-2:] == ["rev-parse", "HEAD"]:
                result.stdout = "abc123\n"
            else:
                result.stdout = ""
            return result

        result = FACTORY_GIT.stage_and_commit_if_dirty(Path("/tmp/wt-test"), "commit message", run_fn=mock_run)
        self.assertEqual(result, "abc123")
        self.assertGreaterEqual(len(calls), 4)

    def test_stage_and_commit_if_dirty_returns_none_when_clean(self) -> None:
        calls: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        result = FACTORY_GIT.stage_and_commit_if_dirty(Path("/tmp/wt-test"), "commit message", run_fn=mock_run)
        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)

    def test_stage_and_commit_with_deleted_file_returns_sha(self) -> None:
        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if cmd[-2:] == ["status", "--porcelain"]:
                result.stdout = "D deleted.ts\n"
            elif cmd[-2:] == ["rev-parse", "HEAD"]:
                result.stdout = "deadbeef\n"
            else:
                result.stdout = ""
            return result

        result = FACTORY_GIT.stage_and_commit_if_dirty(Path("/tmp/wt-test"), "commit message", run_fn=mock_run)
        self.assertIsNotNone(result)
        self.assertEqual(result, "deadbeef")

    def test_cherry_pick_commits_returns_true_on_success(self) -> None:
        calls: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        result = FACTORY_GIT.cherry_pick_commits(["sha1", "sha2"], run_fn=mock_run)
        self.assertEqual(result, (True, ""))
        self.assertEqual(len(calls), 2)

    def test_cherry_pick_commits_returns_false_and_calls_abort_on_failure(self) -> None:
        calls: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[-2:] == ["cherry-pick", "sha1"]:
                raise FACTORY_GIT.subprocess.CalledProcessError(1, cmd, output="", stderr="conflict")
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        result = FACTORY_GIT.cherry_pick_commits(["sha1", "sha2"], run_fn=mock_run)
        self.assertFalse(result[0])
        self.assertIn("sha1", result[1])
        self.assertTrue(any(cmd[-1] == "--abort" for cmd in calls))


class TestCommandImplement(unittest.TestCase):
    def _args(self, slug: str = "test-slug", max_workers: int = 4):
        return SimpleNamespace(slug=slug, max_workers=max_workers)

    def _clean_status(self) -> MagicMock:
        return MagicMock(returncode=0, stdout="", stderr="")

    def test_serial_path_calls_run_serial(self) -> None:
        with patch.object(CMD_IMPLEMENT_MODULE.subprocess, "run", return_value=self._clean_status()), patch.object(
            CMD_IMPLEMENT_MODULE,
            "parse_parallel_task_groups",
            return_value=[{"tasks": ["T001"], "parallel": False, "files": [], "overlap_warning": None}],
        ) as mock_parse, patch.object(CMD_IMPLEMENT_MODULE, "_run_serial", return_value=0) as mock_run_serial:
            result = MODULE.command_implement(self._args())

        self.assertEqual(result, 0)
        mock_parse.assert_called_once_with("test-slug")
        mock_run_serial.assert_called_once_with("test-slug", ["T001"])

    def test_parallel_path_calls_run_parallel(self) -> None:
        group = {"tasks": ["T001", "T002"], "parallel": True, "files": ["a.ts", "b.ts"], "overlap_warning": None}
        with patch.object(CMD_IMPLEMENT_MODULE.subprocess, "run", return_value=self._clean_status()), patch.object(
            CMD_IMPLEMENT_MODULE, "parse_parallel_task_groups", return_value=[group]
        ), patch.object(CMD_IMPLEMENT_MODULE, "_run_parallel", return_value=0) as mock_run_parallel:
            result = MODULE.command_implement(self._args())

        self.assertEqual(result, 0)
        mock_run_parallel.assert_called_once()
        self.assertEqual(mock_run_parallel.call_args.args[0], "test-slug")
        self.assertEqual(mock_run_parallel.call_args.args[1], group)
        self.assertEqual(mock_run_parallel.call_args.kwargs["max_workers"], 4)

    def test_overlap_warning_printed_and_runs_serial(self) -> None:
        group = {
            "tasks": ["T001", "T002"],
            "parallel": False,
            "files": [],
            "overlap_warning": "tasks 1,2 share file foo.ts",
        }
        stderr = io.StringIO()
        with patch.object(CMD_IMPLEMENT_MODULE.subprocess, "run", return_value=self._clean_status()), patch.object(
            CMD_IMPLEMENT_MODULE, "parse_parallel_task_groups", return_value=[group]
        ), patch.object(CMD_IMPLEMENT_MODULE, "_run_serial", return_value=0) as mock_run_serial, redirect_stderr(stderr):
            result = MODULE.command_implement(self._args())

        self.assertEqual(result, 0)
        self.assertIn("tasks 1,2 share file foo.ts", stderr.getvalue())
        mock_run_serial.assert_called_once_with("test-slug", ["T001", "T002"])

    def test_dirty_working_tree_exits_1_without_codex(self) -> None:
        dirty_status = MagicMock(returncode=0, stdout="M foo.ts\n", stderr="")
        with patch.object(CMD_IMPLEMENT_MODULE.subprocess, "run", return_value=dirty_status), patch.object(
            CMD_IMPLEMENT_MODULE, "_run_serial", return_value=0
        ) as mock_run_serial:
            result = MODULE.command_implement(self._args())

        self.assertEqual(result, 1)
        mock_run_serial.assert_not_called()

    def test_empty_groups_prints_nothing_to_implement_and_exits_0(self) -> None:
        stdout = io.StringIO()
        with patch.object(CMD_IMPLEMENT_MODULE.subprocess, "run", return_value=self._clean_status()), patch.object(
            CMD_IMPLEMENT_MODULE, "parse_parallel_task_groups", return_value=[]
        ), redirect_stdout(stdout):
            result = MODULE.command_implement(self._args())

        self.assertEqual(result, 0)
        self.assertIn("nothing to implement", stdout.getvalue())

    def test_nonzero_rc_propagated_immediately(self) -> None:
        groups = [
            {"tasks": ["T001"], "parallel": False, "files": [], "overlap_warning": None},
            {"tasks": ["T002"], "parallel": False, "files": [], "overlap_warning": None},
        ]
        with patch.object(CMD_IMPLEMENT_MODULE.subprocess, "run", return_value=self._clean_status()), patch.object(
            CMD_IMPLEMENT_MODULE, "parse_parallel_task_groups", return_value=groups
        ), patch.object(CMD_IMPLEMENT_MODULE, "_run_serial", side_effect=[1, 0]) as mock_run_serial:
            result = MODULE.command_implement(self._args())

        self.assertEqual(result, 1)
        self.assertEqual(mock_run_serial.call_count, 1)
