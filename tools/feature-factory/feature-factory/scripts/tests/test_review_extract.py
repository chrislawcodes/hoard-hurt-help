import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_state as FACTORY_STATE  # noqa: E402
import factory_cmd_review_extract as FACTORY_CMD_REVIEW_EXTRACT  # noqa: E402


class ReviewExtractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_root = Path(self._tmpdir.name)
        self.runs_root = self.repo_root / "docs" / "workflow" / "feature-runs"
        self.slug = "review-extract"
        self.review_dir = self.runs_root / self.slug / "reviews"
        self.review_dir.mkdir(parents=True, exist_ok=True)

        self._repo_patch = mock.patch.object(FACTORY_STATE, "REPO_ROOT", self.repo_root)
        self._runs_patch = mock.patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", self.runs_root)
        self._cmd_repo_patch = mock.patch.object(FACTORY_CMD_REVIEW_EXTRACT, "REPO_ROOT", self.repo_root)
        self._cmd_runs_patch = mock.patch.object(FACTORY_CMD_REVIEW_EXTRACT, "FACTORY_RUNS_ROOT", self.runs_root)
        self._repo_patch.start()
        self._runs_patch.start()
        self._cmd_repo_patch.start()
        self._cmd_runs_patch.start()
        self.addCleanup(self._repo_patch.stop)
        self.addCleanup(self._runs_patch.stop)
        self.addCleanup(self._cmd_repo_patch.stop)
        self.addCleanup(self._cmd_runs_patch.stop)

    def _write_review(self, stem: str, body: str, *, resolution_status: str = "open") -> Path:
        path = self.review_dir / f"spec.{stem}.review.md"
        path.write_text(
            "---\n"
            f'resolution_status: "{resolution_status}"\n'
            "---\n"
            f"{body}\n",
            encoding="utf-8",
        )
        return path

    def _invoke(self, *, stage: str = "spec", out: Path | None = None, fmt: str = "json") -> tuple[int, str, str]:
        args = argparse.Namespace(slug=self.slug, stage=stage, format=fmt, out=str(out) if out else None)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = FACTORY_CMD_REVIEW_EXTRACT.command_review_extract(args)
        return rc, stdout.getvalue(), stderr.getvalue()

    def _records(self, body: str, *, resolution_status: str = "open") -> list[dict]:
        self._write_review("case", body, resolution_status=resolution_status)
        rc, stdout, stderr = self._invoke()
        self.assertEqual(rc, 0, stderr)
        return json.loads(stdout)

    def test_bullet_bold_finding_extracts_one_record(self) -> None:
        records = self._records("## Findings\n- **HIGH**: bullet finding.\n")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["severity"], "HIGH")
        self.assertEqual(records[0]["frontmatter_status"], "open")

    def test_numbered_list_extracts_one_record_per_finding(self) -> None:
        records = self._records("## Findings\n1. **HIGH**: first\n2. **MEDIUM**: second\n")
        self.assertEqual([record["severity"] for record in records], ["HIGH", "MEDIUM"])

    def test_heading_style_extracts_one_record(self) -> None:
        records = self._records("## Findings\n### HIGH: heading finding\n")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["severity"], "HIGH")

    def test_code_fence_false_positive_is_ignored(self) -> None:
        records = self._records("## Findings\n```\n- HIGH: not a finding\n```\n")
        self.assertEqual(records, [])

    def test_empty_findings_section_returns_no_records(self) -> None:
        records = self._records("## Findings\n")
        self.assertEqual(records, [])

    def test_malformed_frontmatter_returns_exit_2(self) -> None:
        self._write_review(
            "malformed",
            "## Findings\n- **HIGH**: should not matter.\n",
        ).write_text(
            "---\n"
            'resolution_status: "open"\n'
            "## Findings\n"
            "- **HIGH**: should not matter.\n",
            encoding="utf-8",
        )
        rc, _, stderr = self._invoke()
        self.assertEqual(rc, 2)
        self.assertIn("malformed frontmatter", stderr)

    def test_missing_inline_text_uses_following_line(self) -> None:
        records = self._records("## Findings\n- **HIGH**:\n  next line carries the summary.\n")
        self.assertEqual(records[0]["first_line"], "next line carries the summary.")

    def test_next_heading_caps_line_end(self) -> None:
        records = self._records("## Findings\n- **HIGH**: line one\n### Residual Risks\nmore text\n")
        self.assertEqual(len(records), 1)
        self.assertLess(records[0]["line_start"], records[0]["line_end"])

    def test_rejected_frontmatter_status_is_preserved(self) -> None:
        self._write_review(
            "rejected",
            "## Findings\n- **HIGH**: rejected finding.\n",
            resolution_status="rejected",
        )
        rc, stdout, stderr = self._invoke()
        self.assertEqual(rc, 0, stderr)
        records = json.loads(stdout)
        self.assertEqual(records[0]["frontmatter_status"], "rejected")


if __name__ == "__main__":
    unittest.main()
