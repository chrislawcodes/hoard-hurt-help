"""Tests for the 'Findings Pushed Aside' PR-body section — built-in FF summary."""
import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

PR_BODY_SPEC = importlib.util.spec_from_file_location(
    "factory_pr_body", SCRIPTS_DIR / "factory_pr_body.py"
)
assert PR_BODY_SPEC and PR_BODY_SPEC.loader
FPR = importlib.util.module_from_spec(PR_BODY_SPEC)
sys.modules[PR_BODY_SPEC.name] = FPR
PR_BODY_SPEC.loader.exec_module(FPR)


def _concern(
    stage: str = "spec",
    judge: str = "completeness",
    reasoning: str = "something might go wrong in the data path",
    round_raised: int = 3,
    concern_id: str = "abc123def456",
    deferred_reason: str | None = None,
    dismissed_reason: str | None = None,
    addressed_at: int | None = None,
) -> dict:
    return {
        "id": concern_id,
        "stage": stage,
        "judge": judge,
        "reasoning": reasoning,
        "round_raised": round_raised,
        "deferred_reason": deferred_reason,
        "dismissed_reason": dismissed_reason,
        "addressed_at": addressed_at,
        "addressed_by": None,
    }


class FindingsPushedAsideTests(unittest.TestCase):
    def test_no_pushed_aside_returns_empty(self) -> None:
        self.assertEqual(FPR.render_findings_pushed_aside_block([]), [])
        addressed_only = [_concern(addressed_at=12345)]
        self.assertEqual(FPR.render_findings_pushed_aside_block(addressed_only), [])

    def test_deferred_concern_renders_why(self) -> None:
        concerns = [_concern(deferred_reason="Fix requires embedding service — follow-up #789")]
        lines = FPR.render_findings_pushed_aside_block(concerns)
        rendered = "\n".join(lines)
        self.assertIn("Findings Pushed Aside", rendered)
        self.assertIn("deferred", rendered)
        self.assertIn("follow-up #789", rendered)
        self.assertIn("Why it was deferred (fix is follow-up work)", rendered)

    def test_dismissed_concern_renders_why(self) -> None:
        concerns = [_concern(dismissed_reason="False positive — reviewer misread the regex shape")]
        lines = FPR.render_findings_pushed_aside_block(concerns)
        rendered = "\n".join(lines)
        self.assertIn("dismissed", rendered)
        self.assertIn("False positive", rendered)
        self.assertIn("Why it was dismissed (reviewer was wrong)", rendered)

    def test_multiple_pushed_aside_in_stage_order(self) -> None:
        concerns = [
            _concern(stage="tasks", judge="coverage", deferred_reason="Out of scope"),
            _concern(stage="spec", judge="completeness", dismissed_reason="False positive"),
            _concern(stage="plan", judge="architecture", deferred_reason="Follow-up"),
        ]
        lines = FPR.render_findings_pushed_aside_block(concerns)
        rendered = "\n".join(lines)
        spec_idx = rendered.find("spec stage")
        plan_idx = rendered.find("plan stage")
        tasks_idx = rendered.find("tasks stage")
        self.assertLess(spec_idx, plan_idx)
        self.assertLess(plan_idx, tasks_idx)

    def test_mixed_resolved_only_pushed_aside_render(self) -> None:
        """Addressed concerns don't appear in this section — only deferred/dismissed."""
        concerns = [
            _concern(addressed_at=11111, reasoning="addressed item"),
            _concern(deferred_reason="Out of scope", reasoning="deferred item"),
            _concern(dismissed_reason="Reviewer wrong", reasoning="dismissed item"),
        ]
        lines = FPR.render_findings_pushed_aside_block(concerns)
        rendered = "\n".join(lines)
        self.assertNotIn("addressed item", rendered)
        self.assertIn("deferred item", rendered)
        self.assertIn("dismissed item", rendered)

    def test_missing_reason_is_flagged_in_output(self) -> None:
        """An operator that forgot to fill in the reason gets a visible nudge."""
        concerns = [_concern(deferred_reason="")]
        concerns[0]["deferred_reason"] = None  # simulate completely missing reason
        # With neither deferred_reason nor dismissed_reason set but no addressed_at either,
        # the concern would not be classified as pushed-aside. Test the near-miss case:
        # deferred_reason is empty string (set but blank).
        concerns = [_concern(deferred_reason=" ")]
        lines = FPR.render_findings_pushed_aside_block(concerns)
        rendered = "\n".join(lines)
        self.assertIn("no reason provided", rendered)

    def test_included_in_full_pr_body_render(self) -> None:
        """The new section integrates into render_judge_panel_block output."""
        state = {
            "stages": {
                "spec": {
                    "unresolved_concerns": [
                        {
                            "id": "deadbeef1234",
                            "stage": "spec",
                            "judge": "completeness",
                            "reasoning": "a concern that was deferred",
                            "round_raised": 3,
                            "deferred_reason": "Too big for this PR",
                            "dismissed_reason": None,
                            "addressed_at": None,
                            "addressed_by": None,
                        }
                    ]
                }
            }
        }
        body = FPR.render_judge_panel_block(state)
        self.assertIn("Findings Pushed Aside", body)
        self.assertIn("Too big for this PR", body)
        self.assertIn("a concern that was deferred", body)


if __name__ == "__main__":
    unittest.main()
