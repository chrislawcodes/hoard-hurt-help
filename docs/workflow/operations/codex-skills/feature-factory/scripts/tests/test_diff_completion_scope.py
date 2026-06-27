import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_stages as STAGES  # noqa: E402


class PathHelperTests(unittest.TestCase):
    def test_in_scope(self) -> None:
        self.assertTrue(STAGES._path_in_diff_scope("app/engine/x.py", ["app/engine"]))
        self.assertTrue(STAGES._path_in_diff_scope("app/engine", ["app/engine"]))
        self.assertFalse(STAGES._path_in_diff_scope("app/other.py", ["app/engine"]))
        # Empty scope = whole branch -> everything is "in scope".
        self.assertTrue(STAGES._path_in_diff_scope("anything/at/all.py", []))

    def test_bookkeeping(self) -> None:
        self.assertTrue(STAGES._is_diff_bookkeeping_path("STATUS.md"))
        self.assertTrue(
            STAGES._is_diff_bookkeeping_path("docs/workflow/feature-runs/s/closeout.md")
        )
        self.assertTrue(
            STAGES._is_diff_bookkeeping_path("docs/workflow/feature-runs/s/postmortem.md")
        )
        self.assertFalse(STAGES._is_diff_bookkeeping_path("app/engine/x.py"))


class InScopeChangeTests(unittest.TestCase):
    def _call(self, *, ancestor=True, changed=None, scope=None) -> bool:
        with patch.object(STAGES, "is_ancestor_of_head", return_value=ancestor), \
            patch.object(STAGES, "_diff_changed_files", return_value=changed), \
            patch.object(STAGES, "load_scope_manifest", return_value={"paths": scope or []}):
            return STAGES._diff_has_in_scope_change("s", "AAA", "BBB", True)

    def test_no_head_mismatch_is_not_a_change(self) -> None:
        # head_mismatch False short-circuits before any git call.
        self.assertFalse(STAGES._diff_has_in_scope_change("s", "AAA", "AAA", False))

    def test_rewritten_history_forces_rereview(self) -> None:
        self.assertTrue(self._call(ancestor=False, changed=["app/x.py"], scope=["app"]))

    def test_undiffable_forces_rereview(self) -> None:
        self.assertTrue(self._call(ancestor=True, changed=None, scope=["app"]))

    def test_in_scope_code_change_reopens(self) -> None:
        self.assertTrue(
            self._call(ancestor=True, changed=["app/engine/x.py"], scope=["app/engine"])
        )

    def test_bookkeeping_only_does_not_reopen(self) -> None:
        self.assertFalse(
            self._call(
                ancestor=True,
                changed=["docs/workflow/feature-runs/s/closeout.md", "STATUS.md"],
                scope=["app/engine"],
            )
        )

    def test_out_of_scope_code_does_not_reopen(self) -> None:
        self.assertFalse(
            self._call(ancestor=True, changed=["app/other/y.py"], scope=["app/engine"])
        )

    def test_whole_branch_bookkeeping_only_does_not_reopen(self) -> None:
        # No scope (whole-branch diff): bookkeeping allowlist is the guard.
        self.assertFalse(self._call(ancestor=True, changed=["STATUS.md"], scope=[]))

    def test_whole_branch_real_change_reopens(self) -> None:
        self.assertTrue(self._call(ancestor=True, changed=["README.md"], scope=[]))


class StageRepairableWiringTests(unittest.TestCase):
    DIFF_STATE = {"artifact_exists": True, "manifest_exists": True, "healthy": True}

    def _repairable(self, budget: dict) -> bool:
        with patch.object(STAGES, "diff_review_budget_state", return_value=budget):
            return STAGES.stage_repairable("s", "diff", dict(self.DIFF_STATE))

    def test_clean_review_with_only_bookkeeping_is_done(self) -> None:
        self.assertFalse(self._repairable({"in_scope_change": False}))

    def test_in_scope_change_is_repairable(self) -> None:
        self.assertTrue(self._repairable({"in_scope_change": True}))

    def test_unhealthy_is_repairable_regardless(self) -> None:
        with patch.object(STAGES, "diff_review_budget_state", return_value={"in_scope_change": False}):
            state = {"artifact_exists": True, "manifest_exists": True, "healthy": False}
            self.assertTrue(STAGES.stage_repairable("s", "diff", state))


if __name__ == "__main__":
    unittest.main()
