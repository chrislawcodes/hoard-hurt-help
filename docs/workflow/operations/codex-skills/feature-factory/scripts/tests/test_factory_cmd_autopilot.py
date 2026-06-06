"""Tests for factory_cmd_autopilot.command_autopilot.

All subprocesses (codex, gemini, ruff, mypy, pytest, git) are mocked.
No real I/O or network calls are made.
"""
import argparse
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Module loading helpers — follow the pattern used by other FF test files
# ---------------------------------------------------------------------------


def _load(name: str, path: Path):  # type: ignore[return]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"could not load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# Load dependency chain first so that autopilot's imports resolve to the
# same objects that tests patch.
FACTORY_IO = _load("factory_io", SCRIPT_DIR / "factory_io.py")

_REVIEW_SCRIPTS = SCRIPT_DIR.parents[1] / "review-lens" / "scripts"
WORKFLOW_UTILS = _load("workflow_utils", _REVIEW_SCRIPTS / "workflow_utils.py")

FACTORY_STATE = _load("factory_state", SCRIPT_DIR / "factory_state.py")
FACTORY_HEARTBEAT = _load("factory_heartbeat", SCRIPT_DIR / "factory_heartbeat.py")
FACTORY_TELEMETRY = _load("factory_telemetry", SCRIPT_DIR / "factory_telemetry.py")
FACTORY_TELEMETRY_COMMANDS = _load(
    "factory_telemetry_commands", SCRIPT_DIR / "factory_telemetry_commands.py"
)
FACTORY_MUTATING = _load("factory_mutating", SCRIPT_DIR / "factory_mutating.py")
FACTORY_EMIT = _load("factory_emit", SCRIPT_DIR / "factory_emit.py")
FACTORY_PARALLEL = _load("factory_parallel", SCRIPT_DIR / "factory_parallel.py")
FACTORY_STAGES = _load("factory_stages", SCRIPT_DIR / "factory_stages.py")
FACTORY_NEXT_ACTION = _load("factory_next_action", SCRIPT_DIR / "factory_next_action.py")
FACTORY_REVIEW_SPECS = _load("factory_review_specs", SCRIPT_DIR / "factory_review_specs.py")
FACTORY_REVIEW = _load("factory_review", SCRIPT_DIR / "factory_review.py")
FACTORY_GIT = _load("factory_git", SCRIPT_DIR / "factory_git.py")
FACTORY_CMD_CHECKPOINT = _load(
    "factory_cmd_checkpoint", SCRIPT_DIR / "factory_cmd_checkpoint.py"
)
FACTORY_CMD_IMPLEMENT = _load(
    "factory_cmd_implement", SCRIPT_DIR / "factory_cmd_implement.py"
)
AUTOPILOT = _load("factory_cmd_autopilot", SCRIPT_DIR / "factory_cmd_autopilot.py")


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _args(
    slug: str = "test-slug",
    max_iterations: int = 30,
    allow_deliver: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        slug=slug,
        max_iterations=max_iterations,
        allow_deliver=allow_deliver,
    )


def _run_autopilot(args: argparse.Namespace) -> tuple[int, dict]:
    """Run command_autopilot and capture the JSON output."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = AUTOPILOT.command_autopilot(args)
    return rc, json.loads(buf.getvalue())


# ---------------------------------------------------------------------------
# Classification: authoring actions yield without running anything
# ---------------------------------------------------------------------------


class ClassificationTests(unittest.TestCase):
    def _assert_authoring_yield(self, next_action: str) -> None:
        with patch.object(AUTOPILOT, "_current_next_action", return_value=next_action):
            with patch.object(AUTOPILOT, "command_checkpoint") as mock_cp:
                with patch.object(AUTOPILOT, "command_implement") as mock_impl:
                    rc, result = _run_autopilot(_args())
        self.assertEqual(rc, 0)
        self.assertEqual(result["stop_reason"], "needs_authoring")
        self.assertEqual(result["next_action"], next_action)
        self.assertEqual(result["actions_taken"], [])
        mock_cp.assert_not_called()
        mock_impl.assert_not_called()

    def test_author_spec_yields_needs_authoring(self) -> None:
        self._assert_authoring_yield("author_spec")

    def test_author_plan_yields_needs_authoring(self) -> None:
        self._assert_authoring_yield("author_plan")

    def test_author_tasks_yields_needs_authoring(self) -> None:
        self._assert_authoring_yield("author_tasks")

    def test_discover_yields_needs_authoring(self) -> None:
        self._assert_authoring_yield("discover")

    def test_record_parallel_analysis_yields(self) -> None:
        self._assert_authoring_yield("record_parallel_analysis")

    def test_closeout_yields_needs_authoring(self) -> None:
        self._assert_authoring_yield("closeout")

    def test_write_postmortem_yields_needs_authoring(self) -> None:
        self._assert_authoring_yield("write_postmortem")

    def test_update_status_md_yields_needs_authoring(self) -> None:
        self._assert_authoring_yield("update_status_md")

    def test_reconcile_arch_docs_yields_needs_authoring(self) -> None:
        self._assert_authoring_yield("reconcile_arch_docs")

    def test_mechanical_checkpoint_is_auto_run(self) -> None:
        """run_spec_checkpoint is mechanical — autopilot runs it and loops."""
        side_effects = ["run_spec_checkpoint", "deliver"]
        with patch.object(AUTOPILOT, "_current_next_action", side_effect=side_effects):
            with patch.object(AUTOPILOT, "command_checkpoint", return_value=0):
                with patch.object(AUTOPILOT, "_open_reviews_for_stage", return_value=[]):
                    rc, result = _run_autopilot(_args())
        self.assertEqual(rc, 0)
        # Stopped at deliver (the next yield after the checkpoint)
        self.assertEqual(result["stop_reason"], "awaiting_delivery_approval")
        # One action was taken (the checkpoint)
        self.assertEqual(len(result["actions_taken"]), 1)
        self.assertIn("spec", result["actions_taken"][0]["cmd"])

    def test_implement_is_auto_run(self) -> None:
        """dispatch_next_slice_to_codex is mechanical — autopilot runs it."""
        side_effects = ["dispatch_next_slice_to_codex", "deliver"]
        with patch.object(AUTOPILOT, "_current_next_action", side_effect=side_effects):
            with patch.object(AUTOPILOT, "command_implement", return_value=0):
                with patch.object(AUTOPILOT, "_run_preflight", return_value=(0, "")):
                    with patch.object(AUTOPILOT, "command_checkpoint", return_value=0):
                        with patch.object(
                            AUTOPILOT, "_open_reviews_for_stage", return_value=[]
                        ):
                            rc, result = _run_autopilot(_args())
        self.assertEqual(rc, 0)
        self.assertEqual(result["stop_reason"], "awaiting_delivery_approval")
        # implement + preflight + diff checkpoint were all recorded
        cmds = [a["cmd"] for a in result["actions_taken"]]
        self.assertTrue(any("implement" in c for c in cmds))
        self.assertTrue(any("ruff" in c for c in cmds))
        self.assertTrue(any("diff" in c for c in cmds))


# ---------------------------------------------------------------------------
# Checkpoint: open findings → needs_reconcile (never auto-reconciles)
# ---------------------------------------------------------------------------


class CheckpointOpenFindingsTests(unittest.TestCase):
    def test_open_findings_stops_with_needs_reconcile(self) -> None:
        open_review = {
            "path": "/tmp/fake-slug/reviews/spec.codex.review.md",
            "resolution_status": "open",
            "reviewer": "codex",
        }
        with patch.object(
            AUTOPILOT, "_current_next_action", return_value="run_spec_checkpoint"
        ):
            with patch.object(AUTOPILOT, "command_checkpoint", return_value=0):
                with patch.object(
                    AUTOPILOT, "_open_reviews_for_stage", return_value=[open_review]
                ):
                    rc, result = _run_autopilot(_args())
        self.assertEqual(rc, 0)
        self.assertEqual(result["stop_reason"], "needs_reconcile")
        self.assertEqual(result["next_action"], "reconcile_reviews")
        self.assertEqual(result["details"]["stage"], "spec")
        self.assertIn(open_review, result["details"]["open_reviews"])
        # command_checkpoint was called once but autopilot did NOT advance further
        self.assertEqual(len(result["actions_taken"]), 1)

    def test_no_auto_reconcile_after_open_findings(self) -> None:
        """Autopilot must not call reconcile even if it could."""
        open_review = {
            "path": "/tmp/fake/reviews/diff.gemini.review.md",
            "resolution_status": "open",
            "reviewer": "gemini",
        }
        reconcile_mock = MagicMock()
        with patch.object(
            AUTOPILOT, "_current_next_action", return_value="run_diff_checkpoint"
        ):
            with patch.object(AUTOPILOT, "command_checkpoint", return_value=0):
                with patch.object(
                    AUTOPILOT, "_open_reviews_for_stage", return_value=[open_review]
                ):
                    with patch.object(AUTOPILOT, "command_reconcile", reconcile_mock, create=True):
                        rc, result = _run_autopilot(_args())
        reconcile_mock.assert_not_called()
        self.assertEqual(result["stop_reason"], "needs_reconcile")


# ---------------------------------------------------------------------------
# Reviewer-runner failure (non-zero rc from checkpoint)
# ---------------------------------------------------------------------------


class ReviewRunnerFailureTests(unittest.TestCase):
    def test_checkpoint_failure_stops_review_runner_failed(self) -> None:
        with patch.object(
            AUTOPILOT, "_current_next_action", return_value="run_spec_checkpoint"
        ):
            with patch.object(AUTOPILOT, "command_checkpoint", return_value=1):
                rc, result = _run_autopilot(_args())
        self.assertEqual(rc, 0)
        self.assertEqual(result["stop_reason"], "review_runner_failed")
        self.assertEqual(result["details"]["stage"], "spec")
        self.assertEqual(result["details"]["rc"], 1)

    def test_runner_failure_does_not_auto_pass(self) -> None:
        """A failed reviewer must not be silently skipped."""
        call_count = 0

        def _fake_checkpoint(args: argparse.Namespace) -> int:
            nonlocal call_count
            call_count += 1
            return 1  # always fails

        with patch.object(
            AUTOPILOT, "_current_next_action", return_value="run_plan_checkpoint"
        ):
            with patch.object(AUTOPILOT, "command_checkpoint", side_effect=_fake_checkpoint):
                rc, result = _run_autopilot(_args())
        # Autopilot stopped after the first failure; did not retry or skip
        self.assertEqual(call_count, 1)
        self.assertEqual(result["stop_reason"], "review_runner_failed")

    def test_diff_reviewer_failure_after_implement(self) -> None:
        """When the post-implement diff checkpoint fails, stop with review_runner_failed."""
        side_effects = ["dispatch_next_slice_to_codex"]
        with patch.object(AUTOPILOT, "_current_next_action", side_effect=side_effects):
            with patch.object(AUTOPILOT, "command_implement", return_value=0):
                with patch.object(AUTOPILOT, "_run_preflight", return_value=(0, "")):
                    # diff checkpoint fails
                    with patch.object(AUTOPILOT, "command_checkpoint", return_value=1):
                        rc, result = _run_autopilot(_args())
        self.assertEqual(rc, 0)
        self.assertEqual(result["stop_reason"], "review_runner_failed")
        self.assertEqual(result["details"]["stage"], "diff")


# ---------------------------------------------------------------------------
# Implement failure and preflight failure
# ---------------------------------------------------------------------------


class ImplementAndPreflightTests(unittest.TestCase):
    def test_implement_nonzero_stops_implement_failed(self) -> None:
        with patch.object(
            AUTOPILOT, "_current_next_action", return_value="dispatch_next_slice_to_codex"
        ):
            with patch.object(AUTOPILOT, "command_implement", return_value=1):
                rc, result = _run_autopilot(_args())
        self.assertEqual(rc, 0)
        self.assertEqual(result["stop_reason"], "implement_failed")
        self.assertEqual(result["details"]["rc"], 1)
        # No further actions after the failed implement
        cmds = [a["cmd"] for a in result["actions_taken"]]
        self.assertFalse(any("ruff" in c for c in cmds))
        self.assertFalse(any("diff" in c for c in cmds))

    def test_preflight_failure_stops_preflight_failed(self) -> None:
        with patch.object(
            AUTOPILOT, "_current_next_action", return_value="dispatch_next_slice_to_codex"
        ):
            with patch.object(AUTOPILOT, "command_implement", return_value=0):
                with patch.object(
                    AUTOPILOT,
                    "_run_preflight",
                    return_value=(1, "ruff: E501 line too long\n"),
                ):
                    rc, result = _run_autopilot(_args())
        self.assertEqual(rc, 0)
        self.assertEqual(result["stop_reason"], "preflight_failed")
        self.assertEqual(result["details"]["rc"], 1)
        self.assertIn("ruff", result["details"]["output"])
        # diff checkpoint was NOT called after preflight failure
        cmds = [a["cmd"] for a in result["actions_taken"]]
        self.assertFalse(any("diff" in c for c in cmds))

    def test_implement_zero_then_preflight_zero_runs_diff_checkpoint(self) -> None:
        """Green implement + preflight should proceed to the diff checkpoint."""
        with patch.object(
            AUTOPILOT, "_current_next_action", return_value="dispatch_next_slice_to_codex"
        ):
            with patch.object(AUTOPILOT, "command_implement", return_value=0):
                with patch.object(AUTOPILOT, "_run_preflight", return_value=(0, "")):
                    with patch.object(AUTOPILOT, "command_checkpoint", return_value=1):
                        # diff checkpoint fails so we get a deterministic stop
                        rc, result = _run_autopilot(_args())
        self.assertEqual(result["stop_reason"], "review_runner_failed")
        cmds = [a["cmd"] for a in result["actions_taken"]]
        self.assertTrue(any("implement" in c for c in cmds))
        self.assertTrue(any("ruff" in c for c in cmds))
        self.assertTrue(any("diff" in c for c in cmds))


# ---------------------------------------------------------------------------
# Clean implement → clean diff → advance to next yield
# ---------------------------------------------------------------------------


class CleanImplementAdvanceTests(unittest.TestCase):
    def test_clean_implement_and_diff_advances_to_next_yield(self) -> None:
        """After a clean implement+diff, autopilot loops and yields at deliver."""
        side_effects = [
            "dispatch_next_slice_to_codex",  # iteration 1
            "deliver",  # iteration 2 — natural yield
        ]
        with patch.object(AUTOPILOT, "_current_next_action", side_effect=side_effects):
            with patch.object(AUTOPILOT, "command_implement", return_value=0):
                with patch.object(AUTOPILOT, "_run_preflight", return_value=(0, "")):
                    with patch.object(AUTOPILOT, "command_checkpoint", return_value=0):
                        with patch.object(
                            AUTOPILOT, "_open_reviews_for_stage", return_value=[]
                        ):
                            rc, result = _run_autopilot(_args())
        self.assertEqual(rc, 0)
        self.assertEqual(result["stop_reason"], "awaiting_delivery_approval")
        self.assertEqual(result["next_action"], "deliver")
        # Three actions: implement, preflight, diff checkpoint
        self.assertEqual(len(result["actions_taken"]), 3)
        cmds = [a["cmd"] for a in result["actions_taken"]]
        self.assertTrue(any("implement" in c for c in cmds))
        self.assertTrue(any("ruff" in c for c in cmds))
        self.assertTrue(any("diff" in c for c in cmds))

    def test_multiple_clean_slices_then_deliver(self) -> None:
        """Two clean slices then deliver — actions_taken records both slices."""
        side_effects = [
            "dispatch_next_slice_to_codex",  # slice 1
            "dispatch_next_slice_to_codex",  # slice 2
            "deliver",
        ]
        with patch.object(AUTOPILOT, "_current_next_action", side_effect=side_effects):
            with patch.object(AUTOPILOT, "command_implement", return_value=0):
                with patch.object(AUTOPILOT, "_run_preflight", return_value=(0, "")):
                    with patch.object(AUTOPILOT, "command_checkpoint", return_value=0):
                        with patch.object(
                            AUTOPILOT, "_open_reviews_for_stage", return_value=[]
                        ):
                            rc, result = _run_autopilot(_args())
        self.assertEqual(result["stop_reason"], "awaiting_delivery_approval")
        # 3 actions per slice × 2 slices = 6
        self.assertEqual(len(result["actions_taken"]), 6)


# ---------------------------------------------------------------------------
# Max-iterations guard
# ---------------------------------------------------------------------------


class MaxIterationsTests(unittest.TestCase):
    def test_max_iterations_guard_fires(self) -> None:
        # Simulate an infinite mechanical loop (checkpoint never advances)
        with patch.object(
            AUTOPILOT, "_current_next_action", return_value="run_spec_checkpoint"
        ):
            with patch.object(AUTOPILOT, "command_checkpoint", return_value=0):
                with patch.object(
                    AUTOPILOT, "_open_reviews_for_stage", return_value=[]
                ):
                    rc, result = _run_autopilot(_args(max_iterations=3))
        self.assertEqual(rc, 0)
        self.assertEqual(result["stop_reason"], "max_iterations")
        self.assertEqual(result["details"]["max_iterations"], 3)
        # Exactly 3 checkpoint actions taken before the guard fired
        self.assertEqual(len(result["actions_taken"]), 3)

    def test_max_iterations_one(self) -> None:
        with patch.object(
            AUTOPILOT, "_current_next_action", return_value="run_plan_checkpoint"
        ):
            with patch.object(AUTOPILOT, "command_checkpoint", return_value=0):
                with patch.object(
                    AUTOPILOT, "_open_reviews_for_stage", return_value=[]
                ):
                    rc, result = _run_autopilot(_args(max_iterations=1))
        self.assertEqual(result["stop_reason"], "max_iterations")
        self.assertEqual(len(result["actions_taken"]), 1)


# ---------------------------------------------------------------------------
# Deliver never auto-run; merge never attempted
# ---------------------------------------------------------------------------


class DeliverSafetyTests(unittest.TestCase):
    def test_deliver_stops_without_allow_deliver(self) -> None:
        with patch.object(AUTOPILOT, "_current_next_action", return_value="deliver"):
            with patch.object(AUTOPILOT, "command_checkpoint") as mock_cp:
                with patch.object(AUTOPILOT, "command_implement") as mock_impl:
                    rc, result = _run_autopilot(_args(allow_deliver=False))
        self.assertEqual(rc, 0)
        self.assertEqual(result["stop_reason"], "awaiting_delivery_approval")
        self.assertEqual(result["next_action"], "deliver")
        self.assertEqual(result["actions_taken"], [])
        mock_cp.assert_not_called()
        mock_impl.assert_not_called()

    def test_deliver_stops_even_with_allow_deliver_flag(self) -> None:
        """--allow-deliver is reserved but still stops; merge must never be attempted."""
        with patch.object(AUTOPILOT, "_current_next_action", return_value="deliver"):
            rc, result = _run_autopilot(_args(allow_deliver=True))
        self.assertEqual(rc, 0)
        self.assertEqual(result["stop_reason"], "awaiting_delivery_approval")
        self.assertEqual(result["actions_taken"], [])

    def test_done_terminates_cleanly(self) -> None:
        with patch.object(AUTOPILOT, "_current_next_action", return_value="done"):
            rc, result = _run_autopilot(_args())
        self.assertEqual(rc, 0)
        self.assertEqual(result["stop_reason"], "done")
        self.assertEqual(result["next_action"], "done")
        self.assertEqual(result["actions_taken"], [])

    def test_blocked_stops_with_reason(self) -> None:
        state_with_blocked = {
            "blocked": {"active": True, "reason": "waiting for security review"},
        }
        with patch.object(AUTOPILOT, "_current_next_action", return_value="mark_blocked"):
            with patch.object(
                AUTOPILOT,
                "load_workflow_state",
                return_value=state_with_blocked,
            ):
                rc, result = _run_autopilot(_args())
        self.assertEqual(rc, 0)
        self.assertEqual(result["stop_reason"], "blocked")
        self.assertEqual(result["details"]["reason"], "waiting for security review")


# ---------------------------------------------------------------------------
# Structured output shape
# ---------------------------------------------------------------------------


class StructuredOutputTests(unittest.TestCase):
    def test_output_has_all_required_keys(self) -> None:
        with patch.object(AUTOPILOT, "_current_next_action", return_value="deliver"):
            rc, result = _run_autopilot(_args(slug="my-feature"))
        required_keys = {"stop_reason", "next_action", "slug", "actions_taken", "details"}
        self.assertEqual(required_keys, set(result.keys()))

    def test_slug_in_output(self) -> None:
        with patch.object(AUTOPILOT, "_current_next_action", return_value="deliver"):
            rc, result = _run_autopilot(_args(slug="my-feature-456"))
        self.assertEqual(result["slug"], "my-feature-456")

    def test_actions_taken_structure(self) -> None:
        side_effects = ["run_tasks_checkpoint", "deliver"]
        with patch.object(AUTOPILOT, "_current_next_action", side_effect=side_effects):
            with patch.object(AUTOPILOT, "command_checkpoint", return_value=0):
                with patch.object(AUTOPILOT, "_open_reviews_for_stage", return_value=[]):
                    rc, result = _run_autopilot(_args())
        action = result["actions_taken"][0]
        self.assertIn("cmd", action)
        self.assertIn("rc", action)
        self.assertIn("summary", action)
        self.assertEqual(action["rc"], 0)


if __name__ == "__main__":
    unittest.main()
