"""Routing answers (silent-risk / design-settled) in discovery.

For real (required) runs, `discover --complete` is gated on the two routing
answers; legacy runs (required flag never set — the same fail-open convention
the rest of the checklist gate uses) are not gated. The answers persist in the
discovery checklist blob, and a successful completion prints a path
recommendation derived from them (evidence: experiments.md Running Tally).
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
RUN = SCRIPTS_DIR / "run_factory.py"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import factory_cmd_discover as FCD  # noqa: E402
import factory_state as FACTORY_STATE  # noqa: E402
import run_factory as RUN_FACTORY  # noqa: E402


class MissingRoutingItemsHelperTests(unittest.TestCase):
    def test_empty_discovery_misses_both_routing_items(self) -> None:
        missing = FCD._missing_checklist_items({})
        self.assertTrue(any("silent-risk" in m for m in missing))
        self.assertTrue(any("design-settled" in m for m in missing))

    def test_answered_routing_items_not_missing(self) -> None:
        discovery = {
            "checklist": {
                "goal": "g", "audience": "a", "constraints": "c",
                "silent_risk": "yes", "silent_risk_note": "n",
                "design_settled": "no", "design_settled_note": "n",
            },
            "acceptance_criteria": ["sc"],
            "non_goals": ["ng"],
        }
        self.assertEqual(FCD._missing_checklist_items(discovery), [])

    def test_non_yes_no_value_counts_as_missing(self) -> None:
        discovery = {"checklist": {"silent_risk": "maybe", "design_settled": "yes"}}
        missing = FCD._missing_checklist_items(discovery)
        self.assertTrue(any("silent-risk" in m for m in missing))
        self.assertFalse(any("design-settled" in m for m in missing))


class RoutingRecommendationHelperTests(unittest.TestCase):
    def test_missing_answers_return_none(self) -> None:
        self.assertIsNone(FCD._routing_recommendation({}))
        self.assertIsNone(FCD._routing_recommendation({"silent_risk": "yes"}))
        self.assertIsNone(FCD._routing_recommendation("not-a-dict"))

    def test_silent_risk_yes_recommends_full_factory(self) -> None:
        lines = FCD._routing_recommendation(
            {"silent_risk": "yes", "design_settled": "yes"}
        )
        assert lines is not None
        joined = "\n".join(lines)
        self.assertIn("FULL FEATURE FACTORY", joined)
        self.assertNotIn("MIDDLE LANE", joined)

    def test_test_visible_settled_recommends_direct_path(self) -> None:
        lines = FCD._routing_recommendation(
            {"silent_risk": "no", "design_settled": "yes"}
        )
        assert lines is not None
        joined = "\n".join(lines)
        self.assertIn("DIRECT PATH", joined)
        self.assertIn("~2x", joined)

    def test_test_visible_open_design_recommends_middle_lane(self) -> None:
        lines = FCD._routing_recommendation(
            {"silent_risk": "no", "design_settled": "no"}
        )
        assert lines is not None
        joined = "\n".join(lines)
        self.assertIn("MIDDLE LANE", joined)
        self.assertIn("whole-branch review", joined)


class RoutingGateSubprocessTests(unittest.TestCase):
    """CLI-level gate behavior on a real initialized (required) run."""

    def setUp(self) -> None:
        self.runs = tempfile.mkdtemp()
        self.env = {**os.environ, "FF_FACTORY_RUNS_ROOT": self.runs}
        self.slug = "routing-gate-test"
        self._run("init", "--slug", self.slug, "--path", "app")
        # Fill the pre-existing five checklist items so only the routing
        # answers stand between the run and completion.
        self._run(
            "discover", "--slug", self.slug,
            "--goal", "add /healthz", "--audience", "operators",
            "--acceptance-criteria", "200 in <200ms",
            "--non-goal", "not a readiness probe",
            "--constraints", "no DB access",
        )

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(RUN), *args],
            env=self.env, capture_output=True, text=True,
        )

    def test_complete_blocked_without_routing_answers(self) -> None:
        r = self._run("discover", "--slug", self.slug, "--complete")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checklist is incomplete", r.stderr)
        self.assertIn("silent-risk", r.stderr)
        self.assertIn("design-settled", r.stderr)

    def test_complete_succeeds_with_routing_answers(self) -> None:
        self._run(
            "discover", "--slug", self.slug,
            "--silent-risk", "yes", "wrong-key bug would pass tests",
            "--design-settled", "no", "storage shape still open",
        )
        r = self._run("discover", "--slug", self.slug, "--complete")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("complete: yes", r.stdout)

    def test_invalid_answer_value_rejected(self) -> None:
        r = self._run("discover", "--slug", self.slug, "--silent-risk", "maybe", "note")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("'yes' or 'no'", r.stderr)

    def test_whitespace_note_rejected(self) -> None:
        r = self._run("discover", "--slug", self.slug, "--design-settled", "no", "   ")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("non-empty note", r.stderr)

    def test_force_complete_bypasses_routing_answers(self) -> None:
        r = self._run("discover", "--slug", self.slug, "--complete", "--force-complete")
        self.assertEqual(r.returncode, 0, r.stderr)


class RoutingStateInProcessTests(unittest.TestCase):
    """Persistence, legacy fail-open, and completion output, in-process."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.tmp_root = Path(self.tmpdir)
        self.slug = "routing-state-test"
        self._factory_runs_patch = patch.object(
            FACTORY_STATE, "FACTORY_RUNS_ROOT", self.tmp_root
        )
        self._factory_runs_patch.start()
        self.addCleanup(self._factory_runs_patch.stop)
        FACTORY_STATE.workflow_dir(self.slug).mkdir(parents=True, exist_ok=True)
        state = FACTORY_STATE._default_workflow_state()
        FACTORY_STATE.atomic_json_write(
            FACTORY_STATE.factory_state_path(self.slug), state
        )
        self._sync_patch = patch.object(FCD, "ensure_sync", lambda: None)
        self._sync_patch.start()
        self.addCleanup(self._sync_patch.stop)

    def _run(self, argv: list[str]) -> tuple[int, str]:
        parser = RUN_FACTORY.build_parser()
        args = parser.parse_args(argv)
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            try:
                rc = args.func(args) or 0
            except SystemExit as exc:
                rc = int(exc.code) if isinstance(exc.code, int) else 1
        return rc, buf.getvalue()

    def _load_discovery(self) -> dict:
        state = json.loads(
            FACTORY_STATE.factory_state_path(self.slug).read_text(encoding="utf-8")
        )
        return state.get("discovery", {})

    def _fill_full_checklist(self, silent_risk: str, design_settled: str) -> None:
        rc, _ = self._run([
            "discover", "--slug", self.slug,
            "--goal", "g", "--audience", "a",
            "--acceptance-criteria", "sc", "--non-goal", "ng",
            "--constraints", "c",
            "--silent-risk", silent_risk, f"silent-risk note ({silent_risk})",
            "--design-settled", design_settled, f"design note ({design_settled})",
        ])
        self.assertEqual(rc, 0)

    # -- persistence ------------------------------------------------------
    def test_routing_answers_persist_in_checklist_blob(self) -> None:
        rc, _ = self._run([
            "discover", "--slug", self.slug,
            "--silent-risk", "yes", "wrong-key bug would pass tests",
            "--design-settled", "no", "storage shape still open",
        ])
        self.assertEqual(rc, 0)
        checklist = self._load_discovery()["checklist"]
        self.assertEqual(checklist["silent_risk"], "yes")
        self.assertEqual(checklist["silent_risk_note"], "wrong-key bug would pass tests")
        self.assertEqual(checklist["design_settled"], "no")
        self.assertEqual(checklist["design_settled_note"], "storage shape still open")

    def test_answer_is_normalized_to_lowercase(self) -> None:
        rc, _ = self._run(
            ["discover", "--slug", self.slug, "--silent-risk", "YES", "note"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(self._load_discovery()["checklist"]["silent_risk"], "yes")

    def test_routing_flag_alone_is_a_valid_update_and_reopens_completion(self) -> None:
        # Default discovery state starts complete=True; a routing update is a
        # real update (no "requires at least one update" error) and reopens it.
        self.assertTrue(self._load_discovery().get("complete"))
        rc, _ = self._run(
            ["discover", "--slug", self.slug, "--design-settled", "no", "open"]
        )
        self.assertEqual(rc, 0)
        self.assertFalse(self._load_discovery()["complete"])

    def test_clear_resets_routing_answers(self) -> None:
        self._run(["discover", "--slug", self.slug, "--silent-risk", "yes", "note"])
        rc, _ = self._run(["discover", "--slug", self.slug, "--clear"])
        self.assertEqual(rc, 0)
        checklist = self._load_discovery().get("checklist", {})
        self.assertEqual(checklist.get("silent_risk", ""), "")

    def test_clear_cannot_combine_with_routing_update(self) -> None:
        rc, _ = self._run([
            "discover", "--slug", self.slug,
            "--clear", "--silent-risk", "yes", "note",
        ])
        self.assertNotEqual(rc, 0)

    # -- legacy fail-open ---------------------------------------------------
    def test_legacy_run_without_required_flag_is_not_gated(self) -> None:
        # A run whose discovery was never marked required (the legacy /
        # fixture shape — init is what sets required=True on first init, keyed
        # off the missing init SHA) completes without the routing answers.
        rc, out = self._run(["discover", "--slug", self.slug, "--complete"])
        self.assertEqual(rc, 0)
        self.assertIn("complete: yes", out)

    # -- recommendation branches at --complete ------------------------------
    def test_complete_prints_full_factory_for_silent_risk(self) -> None:
        self._fill_full_checklist("yes", "yes")
        rc, out = self._run(["discover", "--slug", self.slug, "--complete"])
        self.assertEqual(rc, 0)
        self.assertIn("FULL FEATURE FACTORY", out)
        self.assertIn("silent-risk: yes", out)

    def test_complete_prints_direct_path_for_settled_test_visible(self) -> None:
        self._fill_full_checklist("no", "yes")
        rc, out = self._run(["discover", "--slug", self.slug, "--complete"])
        self.assertEqual(rc, 0)
        self.assertIn("DIRECT PATH", out)
        self.assertIn("~2x", out)

    def test_complete_prints_middle_lane_for_open_test_visible(self) -> None:
        self._fill_full_checklist("no", "no")
        rc, out = self._run(["discover", "--slug", self.slug, "--complete"])
        self.assertEqual(rc, 0)
        self.assertIn("MIDDLE LANE", out)

    def test_no_routing_block_when_answers_missing(self) -> None:
        rc, out = self._run(
            ["discover", "--slug", self.slug, "--complete", "--force-complete"]
        )
        self.assertEqual(rc, 0)
        for marker in ("FULL FEATURE FACTORY", "MIDDLE LANE", "DIRECT PATH"):
            self.assertNotIn(marker, out)


if __name__ == "__main__":
    unittest.main()
