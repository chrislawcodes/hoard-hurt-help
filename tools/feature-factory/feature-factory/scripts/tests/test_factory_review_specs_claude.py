import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_review_specs as SPECS  # noqa: E402


class ReviewerOverrideTests(unittest.TestCase):
    def test_default_mix_unchanged(self) -> None:
        reviews = SPECS.required_reviews("spec", False, False, False, [])
        reviewers = {r["reviewer"] for r in reviews}
        self.assertIn("codex", reviewers)
        self.assertIn("gemini", reviewers)

    def test_claude_override_restaffs_same_lenses(self) -> None:
        base = SPECS.required_reviews("spec", False, False, False, [])
        claude = SPECS.required_reviews("spec", False, False, False, [], reviewer_override="claude")
        # Same lenses, in the same order — only who reviews changes.
        self.assertEqual([r["lens"] for r in base], [r["lens"] for r in claude])
        self.assertTrue(claude)
        self.assertTrue(all(r["reviewer"] == "claude" for r in claude))
        self.assertTrue(all(r["model"] == SPECS.DEFAULT_CLAUDE_MODEL for r in claude))

    def test_claude_override_on_plan(self) -> None:
        claude = SPECS.required_reviews("plan", False, False, False, [], reviewer_override="claude")
        self.assertTrue(claude)
        self.assertTrue(all(r["reviewer"] == "claude" for r in claude))

    def test_diff_override_gated_by_size(self) -> None:
        # Substantial diff -> one Claude regression review.
        big = SPECS.required_reviews(
            "diff", False, False, False, [],
            diff_changed_lines=120, diff_review_threshold=50, reviewer_override="claude",
        )
        self.assertEqual(len(big), 1)
        self.assertEqual(big[0]["reviewer"], "claude")
        self.assertEqual(big[0]["lens"], "regression-adversarial")
        # Small diff -> no review (preflight + CI are the gate).
        small = SPECS.required_reviews(
            "diff", False, False, False, [],
            diff_changed_lines=10, diff_review_threshold=50, reviewer_override="claude",
        )
        self.assertEqual(small, [])

    def test_fast_path_override(self) -> None:
        claude = SPECS.required_reviews(
            "diff", False, False, False, [], fast=True, reviewer_override="claude"
        )
        self.assertTrue(claude)
        self.assertTrue(all(r["reviewer"] == "claude" for r in claude))

    def test_unknown_override_is_ignored(self) -> None:
        base = SPECS.required_reviews("spec", False, False, False, [])
        other = SPECS.required_reviews("spec", False, False, False, [], reviewer_override="gpt")
        self.assertEqual([r["reviewer"] for r in base], [r["reviewer"] for r in other])

    def test_resolve_override_env_wins(self) -> None:
        with patch.dict(os.environ, {"FF_REVIEWER": "claude"}):
            self.assertEqual(SPECS.resolve_reviewer_override(), "claude")
            # Env beats state.
            self.assertEqual(
                SPECS.resolve_reviewer_override({"review_policy": {"reviewer": "gemini"}}),
                "claude",
            )

    def test_resolve_override_from_state(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            state = {"review_policy": {"reviewer": "claude"}}
            self.assertEqual(SPECS.resolve_reviewer_override(state), "claude")

    def test_resolve_override_none(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(SPECS.resolve_reviewer_override({}))
            self.assertIsNone(SPECS.resolve_reviewer_override(None))


if __name__ == "__main__":
    unittest.main()
