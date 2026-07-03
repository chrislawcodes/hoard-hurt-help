"""Fail-closed integration tests for the structured findings contract.

Proves the contract end to end across the checkpoint plumbing:
  - verify_review_checkpoint passes healthy files that carry the JSON block
    (clean or with findings) and legacy files the regex still understands;
  - verify fails UNPARSEABLE reviews (malformed JSON block, or a non-trivial
    body with no readable findings) with a re-run instruction — never
    auto-accepting them;
  - repair_review_checkpoint.review_is_healthy re-runs unparseable reviews;
  - the checkpoint findings summary reports JSON-sourced counts and never
    claims "no actionable findings" for a review it could not parse.
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

import factory_cmd_checkpoint as CHECKPOINT  # noqa: E402
import factory_state as FACTORY_STATE  # noqa: E402
import repair_review_checkpoint as REPAIR  # noqa: E402
import verify_review_checkpoint as VERIFY  # noqa: E402
import workflow_utils as WU  # noqa: E402


CLEAN_BLOCK = '```json\n{"reviewed": true, "findings": []}\n```'

FINDINGS_BLOCK = (
    "```json\n"
    '{"reviewed": true, "findings": [{"severity": "HIGH", "title": "swallowed error", '
    '"detail": "except returns None"}]}\n'
    "```"
)

MALFORMED_BLOCK = '```json\n{"reviewed": true, "findings": [broken]}\n```'

# > 400 chars, no legacy shapes, no severity vocabulary at line starts.
NON_TRIVIAL_PROSE = (
    "The retry logic in the connector appears to have a subtle flaw where the "
    "backoff window is computed from the wrong timestamp, which could cause a "
    "storm of requests after a deploy. Additionally the pagination cursor is "
    "not persisted between polls, so a restart may replay turns that were "
    "already acknowledged. Both of these deserve a close look before merge, "
    "and the second one in particular could corrupt the standings table if two "
    "workers race on the same match id during the replay window."
)


def _write_review(review_path: Path, artifact: Path, findings_body: str, residual: str = "- None.") -> None:
    """Write a review file that satisfies every verify check except, possibly, findings parseability."""
    artifact_sha = WU.normalized_artifact_hash("spec", artifact)
    frontmatter = "\n".join(
        [
            "---",
            'reviewer: "codex"',
            'lens: "feasibility-adversarial"',
            'stage: "spec"',
            f'artifact_path: "{artifact.resolve()}"',
            f'artifact_sha256: "{artifact_sha}"',
            'repo_root: "."',
            'git_head_sha: "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"',
            'git_base_ref: "origin/main"',
            'git_base_sha: "cafef00dcafef00dcafef00dcafef00dcafef00d"',
            'generation_method: "codex-runner"',
            'resolution_status: "open"',
            'resolution_note: ""',
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
            "# Review: spec feasibility-adversarial",
            "",
            "## Findings",
            "",
            findings_body,
            "",
            "## Residual Risks",
            "",
            residual,
            "",
            "## Resolution",
            "- status: open",
            "- note: ",
            "",
        ]
    )
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(frontmatter + "\n" + body, encoding="utf-8")


class _TempRepoCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.artifact = self.root / "spec.md"
        self.artifact.write_text("# Spec\n\nDeliver the thing.\n", encoding="utf-8")
        self.review = self.root / "reviews" / "spec.codex.feasibility-adversarial.review.md"

    def _verify_rc(self) -> tuple[int, str]:
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


class VerifyFailClosedTests(_TempRepoCase):
    def test_clean_json_block_passes_verify(self) -> None:
        _write_review(self.review, self.artifact, "No issues found.", residual="- None.\n\n" + CLEAN_BLOCK)
        rc, out = self._verify_rc()
        self.assertEqual(rc, 0, out)

    def test_findings_json_block_passes_verify(self) -> None:
        _write_review(
            self.review,
            self.artifact,
            "One problem, described loosely in prose the regex cannot read.",
            residual="- Watch the deploy.\n\n" + FINDINGS_BLOCK,
        )
        rc, out = self._verify_rc()
        self.assertEqual(rc, 0, out)

    def test_legacy_shaped_findings_still_pass_verify(self) -> None:
        _write_review(self.review, self.artifact, "- high: missing index on match_id")
        rc, out = self._verify_rc()
        self.assertEqual(rc, 0, out)

    def test_trivial_legacy_clean_review_still_passes_verify(self) -> None:
        _write_review(self.review, self.artifact, "No findings returned.")
        rc, out = self._verify_rc()
        self.assertEqual(rc, 0, out)

    def test_non_trivial_body_without_findings_signal_fails_verify(self) -> None:
        _write_review(self.review, self.artifact, NON_TRIVIAL_PROSE)
        rc, out = self._verify_rc()
        self.assertEqual(rc, 1)
        self.assertIn("unparseable", out)
        self.assertIn("re-run this review lens", out)

    def test_malformed_json_block_fails_verify_and_never_falls_back(self) -> None:
        # Even with a regex-recognizable finding in the prose, a broken block
        # must fail closed rather than silently degrade to the regex.
        _write_review(
            self.review,
            self.artifact,
            "- high: real finding\n\n" + MALFORMED_BLOCK,
        )
        rc, out = self._verify_rc()
        self.assertEqual(rc, 1)
        self.assertIn("malformed", out)


class RepairHealthTests(_TempRepoCase):
    def _healthy(self) -> bool:
        spec = {"path": str(self.review), "stage": "spec", "reviewer": "codex"}
        with patch.object(REPAIR, "REPO_ROOT", self.root):
            return REPAIR.review_is_healthy(spec, self.artifact.resolve())

    def test_json_block_review_is_healthy(self) -> None:
        _write_review(self.review, self.artifact, "No issues found.", residual="- None.\n\n" + CLEAN_BLOCK)
        self.assertTrue(self._healthy())

    def test_unparseable_review_is_unhealthy_so_repair_reruns_it(self) -> None:
        _write_review(self.review, self.artifact, NON_TRIVIAL_PROSE)
        self.assertFalse(self._healthy())

    def test_malformed_json_block_is_unhealthy(self) -> None:
        _write_review(self.review, self.artifact, "fine prose\n\n" + MALFORMED_BLOCK)
        self.assertFalse(self._healthy())


class FindingsSummaryClassificationTests(unittest.TestCase):
    """The operator-facing summary must reflect the JSON-first classification."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._runs_patch = patch.object(
            FACTORY_STATE, "FACTORY_RUNS_ROOT", Path(self._tmpdir.name)
        )
        self._runs_patch.start()
        self.addCleanup(self._runs_patch.stop)

    def _reviews_dir(self) -> Path:
        path = FACTORY_STATE.reviews_dir("ff-fail-closed-test")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _summary(self, stage: str) -> str:
        stderr_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf):
            CHECKPOINT._print_findings_summary(
                "ff-fail-closed-test", stage, [{"reviewer": "codex", "lens": "x"}]
            )
        return stderr_buf.getvalue()

    def test_json_counts_reported_even_when_prose_has_no_shapes(self) -> None:
        (self._reviews_dir() / "spec.codex.x.review.md").write_text(
            "## Findings\n\nOne loosely-worded problem.\n\n" + FINDINGS_BLOCK + "\n",
            encoding="utf-8",
        )
        output = self._summary("spec")
        self.assertIn("findings raised:", output)
        self.assertIn("1 HIGH", output)

    def test_unparseable_review_is_flagged_not_reported_clean(self) -> None:
        (self._reviews_dir() / "spec.codex.x.review.md").write_text(
            "## Findings\n\n" + NON_TRIVIAL_PROSE + "\n",
            encoding="utf-8",
        )
        output = self._summary("spec")
        self.assertIn("UNPARSEABLE review", output)
        self.assertNotIn("no actionable findings raised", output)


if __name__ == "__main__":
    unittest.main()
