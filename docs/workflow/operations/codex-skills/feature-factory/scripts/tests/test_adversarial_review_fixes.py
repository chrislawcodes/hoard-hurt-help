"""Tests for surviving adversarial-review fixes.

P3-7: deferred reviews appear in Findings Pushed Aside section
P2-5: emoji headings, dash delimiters, bracket-severity shapes all match
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


FACTORY_PR_BODY = _load("factory_pr_body")


class DeferredReviewsInPushedAsideTests(unittest.TestCase):
    """P3-7 — deferred reviews appear in the Findings Pushed Aside section."""

    def test_no_slug_no_deferred_section_returns_empty_for_no_concerns(self) -> None:
        """Without slug and without concerns, render returns empty list."""
        out = FACTORY_PR_BODY.render_findings_pushed_aside_block([])
        self.assertEqual(out, [])

    def test_no_slug_with_concerns_renders_block(self) -> None:
        """With concerns but no slug, section renders without deferred reviews."""
        concerns = [
            {
                "id": "abc123",
                "stage": "spec",
                "judge": "completeness",
                "reasoning": "x",
                "deferred_reason": "scope",
                "dismissed_reason": None,
                "addressed_at": None,
            }
        ]
        out = FACTORY_PR_BODY.render_findings_pushed_aside_block(concerns)
        rendered = "\n".join(out)
        self.assertIn("Findings Pushed Aside", rendered)
        self.assertNotIn("Skipped reviews", rendered)

    def test_deferred_reviews_discovered_via_slug(self) -> None:
        """When a slug is passed, deferred reviews on disk are surfaced."""
        with tempfile.TemporaryDirectory() as tmpdir:
            slug = "test-pushed-aside"
            tmp_repo = Path(tmpdir)
            review_dir = (
                tmp_repo
                / "docs"
                / "workflow"
                / "feature-runs"
                / slug
                / "reviews"
            )
            review_dir.mkdir(parents=True)
            (review_dir / "diff.codex.correctness-adversarial.review.md").write_text(
                '---\n'
                'reviewer: "codex"\n'
                'lens: "correctness-adversarial"\n'
                'stage: "diff"\n'
                'resolution_status: "deferred"\n'
                'resolution_note: "Subprocess timeout — content never generated."\n'
                '---\n\n'
                '# Review: diff correctness-adversarial\n',
                encoding="utf-8",
            )
            (review_dir / "diff.gemini.quality-adversarial.review.md").write_text(
                '---\n'
                'reviewer: "gemini"\n'
                'lens: "quality-adversarial"\n'
                'stage: "diff"\n'
                'resolution_status: "accepted"\n'
                'resolution_note: "No actionable findings."\n'
                '---\n\n'
                '# Review: diff quality-adversarial\n',
                encoding="utf-8",
            )

            def fake_deferred_reviews(slug_arg: str | None) -> list[dict]:
                if slug_arg != slug:
                    return []
                import re as _re
                frontmatter_re = _re.compile(r"^---\n(.*?)\n---", _re.DOTALL)
                out: list[dict] = []
                for p in sorted(review_dir.glob("*.review.md")):
                    content = p.read_text(encoding="utf-8")
                    match = frontmatter_re.match(content)
                    if not match:
                        continue
                    fm = match.group(1)
                    status_m = _re.search(r'resolution_status:\s*"([^"]+)"', fm)
                    if not status_m or status_m.group(1) != "deferred":
                        continue
                    note_m = _re.search(r'resolution_note:\s*"([^"]*)"', fm)
                    reviewer_m = _re.search(r'reviewer:\s*"([^"]+)"', fm)
                    lens_m = _re.search(r'lens:\s*"([^"]+)"', fm)
                    stage_m = _re.search(r'stage:\s*"([^"]+)"', fm)
                    out.append(
                        {
                            "path": str(p),
                            "stage": stage_m.group(1) if stage_m else "?",
                            "reviewer": reviewer_m.group(1) if reviewer_m else "?",
                            "lens": lens_m.group(1) if lens_m else "?",
                            "note": note_m.group(1) if note_m else "",
                        }
                    )
                return out

            with patch.object(FACTORY_PR_BODY, "_deferred_reviews", fake_deferred_reviews):
                lines = FACTORY_PR_BODY.render_findings_pushed_aside_block([], slug=slug)

            rendered = "\n".join(lines)
            self.assertIn("Findings Pushed Aside", rendered)
            self.assertIn("Skipped reviews (runner / tool failures)", rendered)
            self.assertIn("correctness-adversarial", rendered)
            self.assertIn("Subprocess timeout", rendered)
            self.assertNotIn("quality-adversarial", rendered)  # this one was accepted


class RegexShapeNewFindingsTests(unittest.TestCase):
    """P2-5 — emoji headings, dash delimiters, bracket-severity shapes all match."""

    def test_emoji_heading_via_helper(self) -> None:
        frs = _load("factory_review_specs")
        self.assertTrue(
            bool(frs._ACTIONABLE_FINDING_RE.search("### 🚨 high: some finding\n"))
        )
        self.assertTrue(
            bool(frs._ACTIONABLE_FINDING_RE.search("**high** - trailing dash delimiter\n"))
        )
        self.assertTrue(
            bool(frs._ACTIONABLE_FINDING_RE.search("**[high severity]**: text\n"))
        )


if __name__ == "__main__":
    unittest.main()
