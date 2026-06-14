"""Slice 2 — 3-way reconcile helper.

Pre-check + sequential write of review frontmatter, body Resolution block,
and plan.md reconciliation entry. Covers happy path, pre-check failure,
idempotent re-run.
"""
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


import factory_reconcile as FACTORY_RECONCILE  # noqa: E402
import factory_review as FACTORY_REVIEW  # noqa: E402


_FRONTMATTER_TEMPLATE = """---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/x/spec.md"
artifact_sha256: "abc123"
repo_root: "."
git_head_sha: "deadbeef"
git_base_ref: "origin/main"
git_base_sha: "1234"
generation_method: "codex-runner"
resolution_status: "open"
resolution_note: ""
raw_output_path: ""
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

- HIGH: example finding.

## Residual Risks

- example risk.

## Resolution
- status: open
- note:
"""


class ThreeWayReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.tmp_root = Path(self.tmpdir)
        self.review_path = self.tmp_root / "spec.codex.feasibility-adversarial.review.md"
        self.review_path.write_text(_FRONTMATTER_TEMPLATE, encoding="utf-8")
        self.plan_path = self.tmp_root / "plan.md"
        self.plan_path.write_text("# Plan\n\n## Review Reconciliation\n\n", encoding="utf-8")

    def _reconcile(self, status: str, note: str) -> int:
        return FACTORY_RECONCILE.reconcile_review_full(
            review_path=self.review_path,
            plan_path=self.plan_path,
            status=status,
            note=note,
            update_review_script=FACTORY_REVIEW.UPDATE_REVIEW,
            append_reconciliation_script=FACTORY_REVIEW.APPEND_RECONCILIATION,
        )

    def test_happy_path_all_three_in_sync(self) -> None:
        rc = self._reconcile("accepted", "all good")
        self.assertEqual(rc, 0)

        review_content = self.review_path.read_text(encoding="utf-8")
        self.assertIn('resolution_status: "accepted"', review_content)
        self.assertIn('resolution_note: "all good"', review_content)
        # Body Resolution block matches frontmatter.
        body_resolution = review_content.split("## Resolution", 1)[-1]
        self.assertIn("- status: accepted", body_resolution)
        self.assertIn("- note: all good", body_resolution)

        plan_content = self.plan_path.read_text(encoding="utf-8")
        self.assertIn(self.review_path.name, plan_content)
        self.assertIn("status: accepted", plan_content)
        self.assertIn("note: all good", plan_content)

    def test_pre_check_review_does_not_exist(self) -> None:
        self.review_path.unlink()
        rc = self._reconcile("accepted", "x")
        self.assertEqual(rc, 2)
        # plan.md should NOT have been touched.
        self.assertEqual(
            self.plan_path.read_text(encoding="utf-8"),
            "# Plan\n\n## Review Reconciliation\n\n",
        )

    def test_plan_missing_creates_it(self) -> None:
        """When plan.md doesn't exist yet (first-run path), append script creates it.

        Per PR #751 diff-review Codex regression MEDIUM #1 — earlier draft
        hard-failed when plan.md was missing, regressing the legitimate
        first-run case. Now we only require the parent dir to be writable.
        """
        self.plan_path.unlink()
        rc = self._reconcile("accepted", "first run")
        self.assertEqual(rc, 0)
        self.assertTrue(self.plan_path.exists())
        plan_content = self.plan_path.read_text(encoding="utf-8")
        self.assertIn(self.review_path.name, plan_content)

    def test_plan_parent_missing_fails_pre_check(self) -> None:
        """If even the parent dir of plan.md doesn't exist, fail clearly."""
        bad_plan = self.tmp_root / "nonexistent_dir" / "plan.md"
        rc = FACTORY_RECONCILE.reconcile_review_full(
            review_path=self.review_path,
            plan_path=bad_plan,
            status="accepted",
            note="x",
            update_review_script=FACTORY_REVIEW.UPDATE_REVIEW,
            append_reconciliation_script=FACTORY_REVIEW.APPEND_RECONCILIATION,
        )
        self.assertEqual(rc, 2)
        review_content = self.review_path.read_text(encoding="utf-8")
        self.assertIn('resolution_status: "open"', review_content)

    def test_pre_check_plan_read_only(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            # Root bypasses file permission bits, so a 0o444 plan is still
            # writable and the pre-check cannot trigger. CI runs as non-root.
            self.skipTest("root ignores read-only permission bits")
        os.chmod(self.plan_path, 0o444)
        try:
            rc = self._reconcile("accepted", "x")
            self.assertEqual(rc, 2)
            review_content = self.review_path.read_text(encoding="utf-8")
            self.assertIn('resolution_status: "open"', review_content)
        finally:
            os.chmod(self.plan_path, 0o644)

    def test_idempotent_rerun(self) -> None:
        rc1 = self._reconcile("accepted", "ok")
        self.assertEqual(rc1, 0)
        plan_content_after_first = self.plan_path.read_text(encoding="utf-8")
        rc2 = self._reconcile("accepted", "ok")
        self.assertEqual(rc2, 0)
        plan_content_after_second = self.plan_path.read_text(encoding="utf-8")
        # Plan.md content shouldn't grow on re-run (dedup by review path).
        self.assertEqual(plan_content_after_first, plan_content_after_second)

    def test_drift_repair(self) -> None:
        """Start with frontmatter and body block out of sync — helper repairs."""
        # Hand-edit body block to "deferred" while frontmatter stays "open".
        content = self.review_path.read_text(encoding="utf-8")
        content = content.replace(
            "## Resolution\n- status: open\n- note:\n",
            "## Resolution\n- status: deferred\n- note: hand-edit\n",
        )
        self.review_path.write_text(content, encoding="utf-8")

        rc = self._reconcile("accepted", "repair drift")
        self.assertEqual(rc, 0)

        # All three converge.
        review_content = self.review_path.read_text(encoding="utf-8")
        self.assertIn('resolution_status: "accepted"', review_content)
        self.assertIn('resolution_note: "repair drift"', review_content)
        body_resolution = review_content.split("## Resolution", 1)[-1]
        self.assertIn("- status: accepted", body_resolution)
        self.assertIn("- note: repair drift", body_resolution)


if __name__ == "__main__":
    unittest.main()
