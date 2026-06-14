"""Slice 1 — `is_codex_quota_exhaustion` classifier + `write_quota_deferred` write path.

Pin the matcher rules so future regressions are visible immediately.
"""
import sys
import tempfile
import unittest
from pathlib import Path


REVIEW_LENS_DIR = (
    Path(__file__).resolve().parents[3]
    / "review-lens"
    / "scripts"
)
if str(REVIEW_LENS_DIR) not in sys.path:
    sys.path.insert(0, str(REVIEW_LENS_DIR))


import run_gemini_review as GEMINI_REVIEW  # noqa: E402


class CodexQuotaClassifierTests(unittest.TestCase):
    def _check(self, stderr: str = "", stdout: str = "") -> bool:
        return GEMINI_REVIEW.is_codex_quota_exhaustion(stderr, stdout)

    # Phrase-pattern positives
    def test_youve_hit_your_usage_limit(self) -> None:
        self.assertTrue(
            self._check(stderr="ERROR: You've hit your usage limit. Upgrade to Pro.")
        )

    def test_usage_limit_exhausted(self) -> None:
        self.assertTrue(self._check(stderr="usage_limit_exhausted: try again later"))

    def test_quota_exceeded(self) -> None:
        self.assertTrue(self._check(stderr="error: monthly quota exceeded"))

    def test_monthly_quota(self) -> None:
        self.assertTrue(self._check(stderr="Your monthly quota is depleted"))

    def test_case_insensitive(self) -> None:
        self.assertTrue(self._check(stderr="USAGE_LIMIT_EXHAUSTED"))

    # HTTP status + Codex/OpenAI context positives
    def test_429_with_openai_context(self) -> None:
        self.assertTrue(
            self._check(stderr="HTTP 429 from openai.com /v1/chat/completions")
        )

    def test_402_with_codex_context(self) -> None:
        self.assertTrue(self._check(stderr="codex returned 402 Payment Required"))

    def test_429_with_chatgpt_context(self) -> None:
        self.assertTrue(self._check(stderr="429 from chatgpt.com api"))

    # HTTP status WITHOUT Codex/OpenAI context — should NOT match
    def test_429_alone_does_not_match(self) -> None:
        self.assertFalse(self._check(stderr="429 Too Many Requests from upstream"))

    def test_402_alone_does_not_match(self) -> None:
        self.assertFalse(self._check(stderr="402 from billing system"))

    def test_plain_rate_limit_text_alone(self) -> None:
        """Per spec: 'rate limit' alone is NOT enough — needs HTTP status + context."""
        self.assertFalse(self._check(stderr="rate limit triggered for unrelated API"))

    # Genuine non-quota failures should NOT match
    def test_timeout_does_not_match(self) -> None:
        self.assertFalse(self._check(stderr="Codex review timed out after 300s"))

    def test_malformed_json_does_not_match(self) -> None:
        self.assertFalse(
            self._check(stderr="json.JSONDecodeError: expecting value at line 1 col 1")
        )

    def test_empty_inputs(self) -> None:
        self.assertFalse(self._check())

    def test_stdout_alone_can_match(self) -> None:
        """Either stderr or stdout may carry the pattern."""
        self.assertTrue(
            self._check(stdout="429 from openai.com")
        )


class WriteQuotaDeferredTests(unittest.TestCase):
    def test_writes_deferred_status_and_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "spec.codex.feasibility-adversarial.review.md"
            metadata = {
                "reviewer": "codex",
                "lens": "feasibility-adversarial",
                "stage": "spec",
                "artifact_path": "docs/workflow/feature-runs/x/spec.md",
                "artifact_sha256": "abc",
                "repo_root": ".",
                "git_head_sha": "deadbeef",
                "git_base_ref": "origin/main",
                "git_base_sha": "1234",
                "generation_method": "codex-runner",
                "resolution_status": "open",
                "resolution_note": "",
                "raw_output_path": "",
                "narrowed_artifact_path": "",
                "narrowed_artifact_sha256": "",
                "coverage_status": "full",
                "coverage_note": "",
            }
            GEMINI_REVIEW.write_quota_deferred(output_path, metadata)

            content = output_path.read_text(encoding="utf-8")
            self.assertIn('resolution_status: "deferred"', content)
            self.assertIn("Codex quota exhausted", content)
            self.assertIn("https://chatgpt.com/codex/settings/usage", content)
            # Frontmatter and body block must agree (fixes the verify_review_checkpoint
            # 3-way drift problem from prior features).
            body_resolution_idx = content.index("## Resolution")
            body_block = content[body_resolution_idx:]
            self.assertIn("- status: deferred", body_block)


if __name__ == "__main__":
    unittest.main()
