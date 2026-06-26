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
            "## Residual Risks\n\n- Scope may expand during implementation.\n",
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


if __name__ == "__main__":
    unittest.main()
