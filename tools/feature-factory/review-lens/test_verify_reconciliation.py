import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parent / "scripts" / "verify_reconciliation.py"
SPEC = importlib.util.spec_from_file_location("verify_reconciliation_under_test", SCRIPT)
assert SPEC and SPEC.loader
VR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VR)


class VerifyReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = self.root / "plan.md"
        self.reviews = self.root / "reviews"
        self.reviews.mkdir()

    def _review(self, note: str) -> Path:
        path = self.reviews / "a.review.md"
        path.write_text(
            "---\nresolution_status: \"accepted\"\nresolution_note: "
            + note
            + "\n---\n\n## Findings\n\nNone.\n",
            encoding="utf-8",
        )
        return path

    def _plan(self, note: str) -> None:
        self.plan.write_text(
            "# Plan\n\n## Review Reconciliation\n\n"
            f"- review: reviews/a.review.md | status: accepted | note: {note}\n",
            encoding="utf-8",
        )

    def test_escaped_quotes_match_raw_quotes(self) -> None:
        review = self._review('"fixed \\"flapping\\" note"')
        self._plan('fixed "flapping" note')
        entries, errors = VR.parse_reconciliation(self.plan)
        self.assertEqual(errors, [])
        data = VR.parse_frontmatter(review)
        self.assertEqual(VR.canonical_note(entries["reviews/a.review.md"][1]), VR.canonical_note(data["resolution_note"]))

    def test_case_difference_mismatches(self) -> None:
        self.assertNotEqual(VR.canonical_note("Fixed note"), VR.canonical_note("fixed note"))

    def test_whitespace_collapses(self) -> None:
        self.assertEqual(VR.canonical_note("a\t b\r\nc\u00a0d"), "a b c d")

    def test_non_string_note_is_invalid(self) -> None:
        review = self._review("true")
        data = VR.parse_frontmatter(review)
        self.assertIsNone(VR.canonical_note(data["resolution_note"]))

    def test_yaml_fallback_warns(self) -> None:
        review = self._review('"note"')
        with patch.object(VR, "yaml", None), patch.object(VR, "_WARNED_YAML_FALLBACK", False):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                data = VR.parse_frontmatter(review)
        self.assertEqual(data["resolution_note"], "note")
        self.assertIn("yaml not available; falling back to byte comparison", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
