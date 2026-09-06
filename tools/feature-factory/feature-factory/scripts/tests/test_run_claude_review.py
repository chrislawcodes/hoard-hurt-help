import json
import subprocess
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

import factory_state as FACTORY_STATE  # noqa: E402
import run_claude_review as RCR  # noqa: E402
import verify_review_checkpoint as VERIFY  # noqa: E402

# Structured findings contract: every assembled review must carry a valid
# fenced findings JSON block; a clean review is the affirmative
# {"reviewed": true, "findings": []}.
CLEAN_JSON_BLOCK = '```json\n{"reviewed": true, "findings": []}\n```\n'
FINDINGS_JSON_BLOCK = (
    "```json\n"
    '{"reviewed": true, "findings": [{"severity": "HIGH", "title": '
    '"acceptance criteria not measurable", "detail": "no numeric target"}]}\n'
    "```\n"
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


class RunClaudeReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name).resolve()
        _git(self.repo, "init")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "Test")
        (self.repo / "README.md").write_text("seed\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "init")

        self.runs = self.repo / "docs" / "workflow" / "feature-runs"
        self.slug_dir = self.runs / "demo"
        (self.slug_dir / "reviews").mkdir(parents=True)
        self.artifact = self.slug_dir / "spec.md"
        self.artifact.write_text("# Spec\n\nDeliver the thing with clear acceptance criteria.\n", encoding="utf-8")

        patcher = patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", self.runs)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.output = self.slug_dir / "reviews" / "spec.claude.requirements-adversarial.review.md"

    def _argv(self, mode: str, extra: list[str]) -> list[str]:
        return [
            "--mode", mode,
            "--artifact", str(self.artifact),
            "--lens", "requirements-adversarial",
            "--stage", "spec",
            "--output", str(self.output),
            "--workspace-dir", str(self.repo),
            "--git-base-ref", "HEAD",
            *extra,
        ]

    def test_emit_then_assemble_produces_verifiable_review(self) -> None:
        prompt_out = self.slug_dir / "reviews" / "spec.claude.requirements-adversarial.prompt.md"
        rc = RCR.main(self._argv("emit-prompt", ["--prompt-out", str(prompt_out)]))
        self.assertEqual(rc, 0)
        prompt = prompt_out.read_text(encoding="utf-8")
        self.assertIn("## Findings", prompt)
        self.assertIn("requirements-adversarial", prompt)

        response = self.slug_dir / "reviews" / "response.md"
        response.write_text(
            "## Findings\n\n- **HIGH**: acceptance criteria are not measurable.\n\n"
            "## Residual Risks\n\n- Scope may expand during implementation.\n\n"
            + FINDINGS_JSON_BLOCK,
            encoding="utf-8",
        )
        rc = RCR.main(self._argv("assemble", ["--response-file", str(response)]))
        self.assertEqual(rc, 0)
        self.assertTrue(self.output.exists())

        data, body = VERIFY.parse_frontmatter(self.output)
        self.assertEqual(data["reviewer"], "claude")
        self.assertEqual(data["stage"], "spec")
        self.assertEqual(data["lens"], "requirements-adversarial")
        for key in VERIFY.NONEMPTY_KEYS:
            self.assertTrue(data.get(key), f"empty required key: {key}")
        self.assertEqual(VERIFY.missing_sections(body), [])
        status, note = VERIFY.resolution_block_values(body)
        self.assertEqual(status, data["resolution_status"])
        self.assertEqual(note, data["resolution_note"])
        # raw output companion is written and referenced
        self.assertTrue((self.output.with_suffix(self.output.suffix + ".raw.txt")).exists())

    def test_assemble_records_derived_output_tokens(self) -> None:
        response = self.slug_dir / "reviews" / "response.md"
        response.write_text(
            "## Findings\n\n- **HIGH**: x.\n\n## Residual Risks\n\n- none\n\n"
            + FINDINGS_JSON_BLOCK,
            encoding="utf-8",
        )
        jsonl = self.slug_dir / "reviews" / "agent-test.jsonl"
        usage = {"message": {"usage": {
            "input_tokens": 6787,
            "cache_creation_input_tokens": 12862,
            "cache_read_input_tokens": 0,
            "output_tokens": 1,
        }}}
        jsonl.write_text(json.dumps(usage) + "\n", encoding="utf-8")
        rc = RCR.main(self._argv("assemble", [
            "--response-file", str(response),
            "--session-jsonl", str(jsonl),
            "--subagent-total-tokens", "21465",
        ]))
        self.assertEqual(rc, 0)
        rec = FACTORY_STATE.load_workflow_state("demo")["token_usage"][-1]
        self.assertEqual(rec["input_tokens"], 19649)  # 6787 + 12862
        self.assertEqual(rec["cache_read_tokens"], 0)
        self.assertEqual(rec["output_tokens"], 1816)  # 21465 - 19649 - 0
        self.assertEqual(rec["total_tokens"], 21465)

    def test_derive_output_tokens(self) -> None:
        totals = {"input_tokens": 19649, "cache_read_tokens": 0, "output_tokens": 1}
        self.assertEqual(RCR._derive_output_tokens(totals, 21465), 1816)
        # No authoritative total -> fall back to the (under-counted) transcript value.
        self.assertEqual(RCR._derive_output_tokens(totals, None), 1)
        # Guard: a total smaller than the known components never goes below jsonl.
        small = {"input_tokens": 100, "cache_read_tokens": 0, "output_tokens": 5}
        self.assertEqual(RCR._derive_output_tokens(small, 50), 5)

    def test_malformed_response_writes_failure(self) -> None:
        response = self.slug_dir / "reviews" / "bad.md"
        response.write_text("this has no required sections", encoding="utf-8")
        rc = RCR.main(self._argv("assemble", ["--response-file", str(response)]))
        self.assertEqual(rc, 5)

    def test_empty_response_writes_failure(self) -> None:
        response = self.slug_dir / "reviews" / "empty.md"
        response.write_text("   \n", encoding="utf-8")
        rc = RCR.main(self._argv("assemble", ["--response-file", str(response)]))
        self.assertEqual(rc, 5)

    def test_missing_findings_json_block_fails_assemble(self) -> None:
        # Sections are fine, but the required findings JSON block is absent —
        # assemble must fail closed so an unparseable review never enters the
        # checkpoint as if it were clean.
        response = self.slug_dir / "reviews" / "no-block.md"
        response.write_text(
            "## Findings\n\n- **HIGH**: something real.\n\n## Residual Risks\n\n- none\n",
            encoding="utf-8",
        )
        rc = RCR.main(self._argv("assemble", ["--response-file", str(response)]))
        self.assertEqual(rc, 5)
        failure = self.output.read_text(encoding="utf-8")
        self.assertIn("findings contract", failure)
        self.assertIn('resolution_status: "failed"', failure)

    def test_malformed_findings_json_block_fails_assemble(self) -> None:
        response = self.slug_dir / "reviews" / "bad-block.md"
        response.write_text(
            "## Findings\n\n- **HIGH**: something real.\n\n## Residual Risks\n\n- none\n\n"
            '```json\n{"reviewed": true, "findings": [broken]}\n```\n',
            encoding="utf-8",
        )
        rc = RCR.main(self._argv("assemble", ["--response-file", str(response)]))
        self.assertEqual(rc, 5)
        failure = self.output.read_text(encoding="utf-8")
        self.assertIn("malformed findings JSON block", failure)

    def test_clean_review_requires_affirmative_clean_block(self) -> None:
        # A clean review with the affirmative block assembles fine.
        response = self.slug_dir / "reviews" / "clean.md"
        response.write_text(
            "## Findings\n\nNo findings.\n\n## Residual Risks\n\n- none\n\n"
            + CLEAN_JSON_BLOCK,
            encoding="utf-8",
        )
        rc = RCR.main(self._argv("assemble", ["--response-file", str(response)]))
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
