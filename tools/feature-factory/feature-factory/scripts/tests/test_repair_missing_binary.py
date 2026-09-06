import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]  # feature-factory/scripts
REVIEW_LENS = SCRIPT_DIR.parents[1] / "review-lens" / "scripts"
for _p in (str(SCRIPT_DIR), str(REVIEW_LENS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import repair_review_checkpoint as REPAIR  # noqa: E402


class MissingBinaryMessageTests(unittest.TestCase):
    def test_missing_gemini_returns_clear_message(self) -> None:
        with patch.object(REPAIR.shutil, "which", return_value=None):
            result = REPAIR._missing_binary_message(
                {"reviewer": "gemini", "path": "reviews/spec.gemini.x.review.md"}
            )
        self.assertIsNotNone(result)
        reviewer, message = result
        self.assertEqual(reviewer, "gemini")
        self.assertIn("gemini CLI not found", message)
        self.assertIn("FF_REVIEWER=claude", message)  # points at the no-CLI path

    def test_missing_codex_returns_clear_message(self) -> None:
        with patch.object(REPAIR.shutil, "which", return_value=None):
            result = REPAIR._missing_binary_message(
                {"reviewer": "codex", "path": "reviews/spec.codex.y.review.md"}
            )
        self.assertIsNotNone(result)
        reviewer, message = result
        self.assertEqual(reviewer, "codex")
        self.assertIn("codex CLI not found", message)

    def test_present_binary_returns_none(self) -> None:
        with patch.object(REPAIR.shutil, "which", return_value="/usr/bin/gemini"):
            self.assertIsNone(
                REPAIR._missing_binary_message({"reviewer": "gemini", "path": "p"})
            )


if __name__ == "__main__":
    unittest.main()
