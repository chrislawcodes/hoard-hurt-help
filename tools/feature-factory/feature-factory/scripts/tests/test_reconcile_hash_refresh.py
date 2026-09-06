"""Reconcile-driven artifact hash refresh (post-reconcile stale-artifact fix).

Proves both halves of the fix end to end against the real
verify_review_checkpoint gate:

  (a) A reconcile-applied artifact change re-records the review's artifact hash,
      so the stage stops reading `repairable` and state carries a
      `reconcile_refreshed` annotation (old→new sha).
  (b) An out-of-band edit with NO reconcile keeps its stale recorded hash and
      still fails closed exactly as today.

The gate (verify_review_checkpoint) is driven directly, mirroring the fixture
style in test_findings_fail_closed.py. Feature-run state writes are routed to a
temp runs root by the package conftest's autouse isolation fixture.
"""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]  # feature-factory/scripts
REVIEW_LENS = SCRIPT_DIR.parents[1] / "review-lens" / "scripts"
for _p in (str(SCRIPT_DIR), str(REVIEW_LENS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import factory_review as FR  # noqa: E402
import factory_state as FACTORY_STATE  # noqa: E402
import verify_review_checkpoint as VERIFY  # noqa: E402
import workflow_utils as WU  # noqa: E402


CLEAN_BLOCK = '```json\n{"reviewed": true, "findings": []}\n```'


def _write_review(
    review_path: Path,
    artifact: Path,
    artifact_sha: str,
    *,
    stage: str = "spec",
    lens: str = "feasibility-adversarial",
    status: str = "accepted",
    note: str = "applied fix",
) -> None:
    """Write a review file that satisfies every verify_review_checkpoint check."""
    frontmatter = "\n".join(
        [
            "---",
            'reviewer: "codex"',
            f'lens: "{lens}"',
            f'stage: "{stage}"',
            f'artifact_path: "{artifact.resolve()}"',
            f'artifact_sha256: "{artifact_sha}"',
            'repo_root: "."',
            'git_head_sha: "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"',
            'git_base_ref: "origin/main"',
            'git_base_sha: "cafef00dcafef00dcafef00dcafef00dcafef00d"',
            'generation_method: "codex-runner"',
            f'resolution_status: "{status}"',
            f'resolution_note: "{note}"',
            'raw_output_path: ""',
            'narrowed_artifact_path: ""',
            'narrowed_artifact_sha256: ""',
            'coverage_status: "full"',
            'coverage_note: ""',
            "---",
        ]
    )
    body = "\n".join(
        [
            "",
            f"# Review: {stage} {lens}",
            "",
            "## Findings",
            "",
            "No issues found.",
            "",
            "## Residual Risks",
            "",
            "- None.",
            "",
            CLEAN_BLOCK,
            "",
            "## Resolution",
            f"- status: {status}",
            f"- note: {note}",
            "",
        ]
    )
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(frontmatter + "\n" + body, encoding="utf-8")


class ReconcileHashRefreshTests(unittest.TestCase):
    slug = "reconcile-refresh-test"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.artifact = self.root / "spec.md"
        self.artifact.write_text("# Spec\n\nDeliver the thing.\n", encoding="utf-8")
        self.review = self.root / "reviews" / "spec.codex.feasibility-adversarial.review.md"
        self.v1_sha = WU.normalized_artifact_hash("spec", self.artifact)
        _write_review(self.review, self.artifact, self.v1_sha)

    def _verify_rc(self, stage: str = "spec") -> tuple[int, str]:
        argv = [
            "verify_review_checkpoint.py",
            "--artifact",
            str(self.artifact),
            "--required-review",
            str(self.review),
        ]
        out = io.StringIO()
        with patch.object(VERIFY, "REPO_ROOT", self.root), patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(out):
                rc = VERIFY.main()
        return rc, out.getvalue()

    def _recorded_sha(self) -> str:
        data, _ = FACTORY_STATE.parse_review_frontmatter(self.review)
        return data.get("artifact_sha256", "")

    # -- Property (a): reconcile-applied change clears repairable + annotates --

    def test_reconcile_applied_change_refreshes_hash_and_clears_repairable(self) -> None:
        # Baseline: review matches the artifact it was run against.
        rc, out = self._verify_rc()
        self.assertEqual(rc, 0, out)

        # Apply an accepted finding to the artifact (this is what reconcile blesses).
        self.artifact.write_text(
            "# Spec\n\nDeliver the thing.\n\nAdd a dedicated betrayal_bonus key.\n",
            encoding="utf-8",
        )
        v2_sha = WU.normalized_artifact_hash("spec", self.artifact)
        self.assertNotEqual(v2_sha, self.v1_sha)

        # Before the refresh the stage reads stale (i.e. `repairable`).
        rc_stale, out_stale = self._verify_rc()
        self.assertEqual(rc_stale, 1)
        self.assertIn("is stale", out_stale)

        # Reconcile's sanctioned refresh re-records the hash.
        refreshed = FR.refresh_reconciled_artifact_hashes(self.slug, [self.review])

        # Stage no longer reads repairable.
        rc_ok, out_ok = self._verify_rc()
        self.assertEqual(rc_ok, 0, out_ok)
        self.assertEqual(self._recorded_sha(), v2_sha)

        # Return value carries the old→new transition.
        self.assertEqual(len(refreshed), 1)
        entry = refreshed[0]
        self.assertEqual(entry["stage"], "spec")
        self.assertEqual(entry["old_sha"], self.v1_sha)
        self.assertEqual(entry["new_sha"], v2_sha)
        self.assertTrue(entry["ts"])
        self.assertTrue(entry["review"].endswith("spec.codex.feasibility-adversarial.review.md"))

        # State carries the reconcile_refreshed annotation.
        state = FACTORY_STATE.load_workflow_state(self.slug)
        trail = state.get(FR.RECONCILE_REFRESHED_KEY, [])
        self.assertEqual(len(trail), 1)
        self.assertEqual(trail[0]["old_sha"], self.v1_sha)
        self.assertEqual(trail[0]["new_sha"], v2_sha)

    # -- Property (b): out-of-band edit with no reconcile still fails closed --

    def test_out_of_band_edit_without_reconcile_fails_closed(self) -> None:
        # Edit the artifact but never run reconcile — the classic out-of-band edit.
        self.artifact.write_text(
            "# Spec\n\nDeliver the thing.\n\nSomeone edited this outside the workflow.\n",
            encoding="utf-8",
        )

        # The recorded hash is untouched and the gate still fails closed.
        rc, out = self._verify_rc()
        self.assertEqual(rc, 1)
        self.assertIn("is stale", out)
        self.assertEqual(self._recorded_sha(), self.v1_sha)

        # No reconcile ran, so no annotation was written.
        state = FACTORY_STATE.load_workflow_state(self.slug)
        self.assertEqual(state.get(FR.RECONCILE_REFRESHED_KEY, []), [])

    # -- Guard: refresh only fires when the artifact actually changed --

    def test_refresh_is_noop_when_artifact_unchanged(self) -> None:
        refreshed = FR.refresh_reconciled_artifact_hashes(self.slug, [self.review])
        self.assertEqual(refreshed, [])
        self.assertEqual(self._recorded_sha(), self.v1_sha)
        state = FACTORY_STATE.load_workflow_state(self.slug)
        self.assertEqual(state.get(FR.RECONCILE_REFRESHED_KEY, []), [])


class ReconcilePlanReconciliationSectionTests(unittest.TestCase):
    """A plan's ## Review Reconciliation section is excluded from the hash, so
    reconcile appending its own entry there must NOT spuriously re-bless the
    plan (only a real plan-body edit should refresh)."""

    slug = "reconcile-refresh-plan-test"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.plan = self.root / "plan.md"
        self.plan.write_text(
            "# Plan\n\nBuild it in two loops.\n\n## Review Reconciliation\n\n",
            encoding="utf-8",
        )
        self.review = self.root / "reviews" / "plan.codex.implementation-adversarial.review.md"
        self.plan_sha = WU.normalized_artifact_hash("plan", self.plan)
        _write_review(
            self.review,
            self.plan,
            self.plan_sha,
            stage="plan",
            lens="implementation-adversarial",
        )

    def test_appending_reconciliation_entry_does_not_trigger_refresh(self) -> None:
        # Simulate what reconcile_review_full appends to plan.md — only the
        # excluded reconciliation section grows, so the normalized hash is stable.
        self.plan.write_text(
            "# Plan\n\nBuild it in two loops.\n\n## Review Reconciliation\n\n"
            "- review: plan.codex.implementation-adversarial.review.md status: accepted note: ok\n",
            encoding="utf-8",
        )
        refreshed = FR.refresh_reconciled_artifact_hashes(self.slug, [self.review])
        self.assertEqual(refreshed, [])
        data, _ = FACTORY_STATE.parse_review_frontmatter(self.review)
        self.assertEqual(data.get("artifact_sha256", ""), self.plan_sha)

    def test_plan_body_edit_does_trigger_refresh(self) -> None:
        # A real edit to the plan body (outside the reconciliation section) changes
        # the normalized hash and must refresh.
        self.plan.write_text(
            "# Plan\n\nBuild it in ONE loop to fix the under-count.\n\n"
            "## Review Reconciliation\n\n",
            encoding="utf-8",
        )
        new_sha = WU.normalized_artifact_hash("plan", self.plan)
        self.assertNotEqual(new_sha, self.plan_sha)
        refreshed = FR.refresh_reconciled_artifact_hashes(self.slug, [self.review])
        self.assertEqual(len(refreshed), 1)
        self.assertEqual(refreshed[0]["new_sha"], new_sha)
        data, _ = FACTORY_STATE.parse_review_frontmatter(self.review)
        self.assertEqual(data.get("artifact_sha256", ""), new_sha)


if __name__ == "__main__":
    unittest.main()
