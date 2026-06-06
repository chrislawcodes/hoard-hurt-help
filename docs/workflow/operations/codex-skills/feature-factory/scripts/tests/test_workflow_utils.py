"""Tests for review-lens/scripts/workflow_utils.py."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


WORKFLOW_UTILS_PATH = (
    Path(__file__).resolve().parents[3] / "review-lens" / "scripts" / "workflow_utils.py"
)
WORKFLOW_UTILS_SPEC = importlib.util.spec_from_file_location(
    "workflow_utils_for_tests", WORKFLOW_UTILS_PATH
)
assert WORKFLOW_UTILS_SPEC and WORKFLOW_UTILS_SPEC.loader
WORKFLOW_UTILS = importlib.util.module_from_spec(WORKFLOW_UTILS_SPEC)
sys.modules[WORKFLOW_UTILS_SPEC.name] = WORKFLOW_UTILS
WORKFLOW_UTILS_SPEC.loader.exec_module(WORKFLOW_UTILS)


class NormalizedArtifactTextTests(unittest.TestCase):
    """Plan reconciliation is metadata and should not stale plan reviews."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def _write(self, name: str, body: str) -> Path:
        path = Path(self._tmpdir.name) / name
        path.write_text(body, encoding="utf-8")
        return path

    def _assert_reconciliation_stripped(self, stage: str) -> None:
        body_without = "# Artifact\n\nSubstantive body.\n\n## Next Steps\n\nMore.\n"
        reconciliation = (
            "## Review Reconciliation\n\n"
            "- review: reviews/a.review.md | status: accepted | note: note one\n"
        )
        # When reconciliation is at the end:
        path_tail = self._write(f"{stage}-tail.md", body_without + "\n" + reconciliation)
        self.assertEqual(
            WORKFLOW_UTILS.normalized_artifact_hash(stage, self._write(f"{stage}-plain.md", body_without)),
            WORKFLOW_UTILS.normalized_artifact_hash(stage, path_tail),
            f"stage={stage}: trailing Review Reconciliation must not affect hash",
        )
        # When another section follows reconciliation:
        path_mid = self._write(
            f"{stage}-mid.md",
            "# Artifact\n\nSubstantive body.\n\n"
            + reconciliation
            + "\n## Next Steps\n\nMore.\n",
        )
        self.assertEqual(
            WORKFLOW_UTILS.normalized_artifact_hash(stage, self._write(f"{stage}-plain.md", body_without)),
            WORKFLOW_UTILS.normalized_artifact_hash(stage, path_mid),
            f"stage={stage}: inlined Review Reconciliation must not affect hash",
        )

    def test_plan_reconciliation_stripped(self) -> None:
        self._assert_reconciliation_stripped("plan")

    def test_non_plan_reconciliation_not_stripped(self) -> None:
        body = "# Spec\n\nBody.\n\n## Review Reconciliation\n\n- review: a\n"
        path = self._write("spec.md", body)
        import hashlib
        self.assertEqual(
            WORKFLOW_UTILS.normalized_artifact_hash("spec", path),
            hashlib.sha256(body.encode("utf-8")).hexdigest(),
        )

    def test_body_without_reconciliation_unchanged(self) -> None:
        body = "# Artifact\n\nNo reconciliation section here.\n"
        path = self._write("plain.md", body)
        # Hash must be the sha256 of the raw text byte-for-byte
        import hashlib

        expected = hashlib.sha256(body.encode("utf-8")).hexdigest()
        for stage in ("spec", "plan", "tasks", "closeout", "diff"):
            self.assertEqual(
                WORKFLOW_UTILS.normalized_artifact_hash(stage, path),
                expected,
                f"stage={stage}: text without reconciliation must hash to raw sha",
            )

    def test_change_to_substantive_body_changes_hash(self) -> None:
        """Make sure stripping reconciliation doesn't accidentally strip the
        substantive body or collapse different artifacts into the same hash."""
        body_a = "# Artifact\n\nBody A.\n\n## Review Reconciliation\n\n- review: a\n"
        body_b = "# Artifact\n\nBody B.\n\n## Review Reconciliation\n\n- review: a\n"
        path_a = self._write("a.md", body_a)
        path_b = self._write("b.md", body_b)
        for stage in ("plan",):
            self.assertNotEqual(
                WORKFLOW_UTILS.normalized_artifact_hash(stage, path_a),
                WORKFLOW_UTILS.normalized_artifact_hash(stage, path_b),
                f"stage={stage}: substantive body differences must change hash",
            )

    def test_duplicate_reconciliation_heading_falls_back_to_full_hash(self) -> None:
        body = "# Plan\n\nBody.\n\n## Review Reconciliation\n\nA\n\n## Review Reconciliation\n\nB\n"
        path = self._write("dup.md", body)
        import hashlib
        self.assertEqual(
            WORKFLOW_UTILS.normalized_artifact_hash("plan", path),
            hashlib.sha256(body.encode("utf-8")).hexdigest(),
        )

    def test_heading_spacing_is_allowed(self) -> None:
        body_without = "# Plan\n\nBody.\n\n## Next\n\nMore.\n"
        path = self._write(
            "spaced.md",
            "# Plan\n\nBody.\n\n##   Review Reconciliation   \n\n- review: a\n\n## Next\n\nMore.\n",
        )
        self.assertEqual(
            WORKFLOW_UTILS.normalized_artifact_hash("plan", self._write("plain2.md", body_without)),
            WORKFLOW_UTILS.normalized_artifact_hash("plan", path),
        )

    def test_plan_artifact_hash_matches_current_normalized_hash(self) -> None:
        path = self._write(
            "plan-current.md",
            "# Plan\n\nBody.\n\n## Review Reconciliation\n\n- review: a\n",
        )
        data = {
            "artifact_sha256": WORKFLOW_UTILS.normalized_artifact_hash("plan", path),
            "narrowed_artifact_sha256": "",
        }
        self.assertTrue(WORKFLOW_UTILS.artifact_hash_matches("plan", path, data))

    def test_plan_artifact_hash_matches_legacy_full_hash_without_narrowed_hash(self) -> None:
        path = self._write(
            "plan-legacy.md",
            "# Plan\n\nBody.\n\n## Review Reconciliation\n\n- review: a\n",
        )
        data = {
            "artifact_sha256": WORKFLOW_UTILS.full_artifact_hash(path),
            "narrowed_artifact_sha256": "",
        }
        self.assertTrue(WORKFLOW_UTILS.artifact_hash_matches("plan", path, data))

    def test_plan_artifact_hash_rejects_legacy_full_hash_after_narrowed_hash_exists(self) -> None:
        path = self._write(
            "plan-new.md",
            "# Plan\n\nBody.\n\n## Review Reconciliation\n\n- review: a\n",
        )
        data = {
            "artifact_sha256": WORKFLOW_UTILS.full_artifact_hash(path),
            "narrowed_artifact_sha256": "present",
        }
        self.assertFalse(WORKFLOW_UTILS.artifact_hash_matches("plan", path, data))


if __name__ == "__main__":
    unittest.main()
