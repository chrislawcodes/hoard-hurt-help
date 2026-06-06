import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "factory_review_specs.py"
SPEC = importlib.util.spec_from_file_location("factory_review_specs", SCRIPT_PATH)
assert SPEC and SPEC.loader
FRS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FRS
SPEC.loader.exec_module(FRS)


def _review(body: str) -> Path:
    """Write a tiny review-shaped file and return the path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        suffix=".review.md",
    )
    tmp.write(body)
    tmp.flush()
    tmp.close()
    return Path(tmp.name)


class ActionableFindingRegexPositiveTests(unittest.TestCase):
    """Every supported finding shape must be detected."""

    def test_bullet_colon_high(self) -> None:
        self.assertTrue(FRS.detect_actionable_findings(_review("- high: missing index\n")))

    def test_bullet_colon_medium(self) -> None:
        self.assertTrue(FRS.detect_actionable_findings(_review("- medium: stale cache\n")))

    def test_bullet_bracket_tag_colon(self) -> None:
        self.assertTrue(
            FRS.detect_actionable_findings(_review("- [code-confirmed] high: stale cache\n"))
        )

    def test_bullet_bare_plus_bracket_tag_upper(self) -> None:
        self.assertTrue(
            FRS.detect_actionable_findings(
                _review("- HIGH [CODE-CONFIRMED]: bug in path handling\n")
            )
        )

    def test_bullet_bold_severity(self) -> None:
        self.assertTrue(
            FRS.detect_actionable_findings(_review("- **HIGH**: path handler missing\n"))
        )

    def test_bullet_bold_severity_with_tag(self) -> None:
        self.assertTrue(
            FRS.detect_actionable_findings(
                _review("- **HIGH [CODE-CONFIRMED]**: path handler missing\n")
            )
        )

    def test_table_cell_high(self) -> None:
        self.assertTrue(FRS.detect_actionable_findings(_review("| **HIGH** | some text |\n")))

    def test_table_cell_critical(self) -> None:
        self.assertTrue(
            FRS.detect_actionable_findings(_review("| **CRITICAL** | some text |\n"))
        )

    def test_numbered_list_bold_severity(self) -> None:
        self.assertTrue(
            FRS.detect_actionable_findings(_review("1. **HIGH**: missing index on foo\n"))
        )

    def test_numbered_list_plain_severity(self) -> None:
        """Plain numbered list: '1. MEDIUM: text' without bold — produced by Codex edge-cases lens."""
        self.assertTrue(
            FRS.detect_actionable_findings(_review("1. MEDIUM: something is wrong\n"))
        )
        self.assertTrue(
            FRS.detect_actionable_findings(_review("2. HIGH: another issue\n"))
        )

    def test_numbered_list_plain_with_bracket_tag(self) -> None:
        self.assertTrue(
            FRS.detect_actionable_findings(_review("1. HIGH [CODE-CONFIRMED]: bug\n"))
        )

    def test_heading_severity(self) -> None:
        self.assertTrue(FRS.detect_actionable_findings(_review("### HIGH: missing index\n")))

    def test_heading_with_rank_prefix_and_colon(self) -> None:
        """Rank-prefixed heading with colon is actionable — but bare trailing
        word (``### 1. HIGH concern``) is NOT, to avoid title false positives
        like ``### HIGH availability target`` (round-2 Codex edge-cases LOW)."""
        self.assertTrue(FRS.detect_actionable_findings(_review("### 1. HIGH: concern\n")))

    def test_paragraph_bold_prefix(self) -> None:
        self.assertTrue(FRS.detect_actionable_findings(_review("**HIGH**: broken join\n")))

    def test_paragraph_bold_prefix_with_tag(self) -> None:
        self.assertTrue(
            FRS.detect_actionable_findings(
                _review("**HIGH [CODE-CONFIRMED]**: duplicate handler\n")
            )
        )

    def test_inline_severity_field_bold(self) -> None:
        self.assertTrue(
            FRS.detect_actionable_findings(_review("**Severity**: HIGH\nmore text\n"))
        )

    def test_inline_severity_field_bold_alternate(self) -> None:
        self.assertTrue(FRS.detect_actionable_findings(_review("**Severity:** HIGH\n")))

    def test_inline_severity_field_plain(self) -> None:
        self.assertTrue(FRS.detect_actionable_findings(_review("Severity: HIGH\n")))

    def test_heading_with_emoji_prefix(self) -> None:
        """Adversarial-review finding: '### 🚨 HIGH:' shape used by some LLM reviewers."""
        self.assertTrue(FRS.detect_actionable_findings(_review("### 🚨 HIGH: missing index\n")))
        self.assertTrue(FRS.detect_actionable_findings(_review("### 🚨 1. HIGH: missing index\n")))

    def test_bold_prefix_with_dash_delimiter(self) -> None:
        """Adversarial-review finding: '**HIGH** - rest-of-line' (dash instead of colon)."""
        self.assertTrue(
            FRS.detect_actionable_findings(_review("**HIGH** - missing rollback path\n"))
        )

    def test_bold_bracket_severity_prefix(self) -> None:
        """Adversarial-review finding: '**[HIGH SEVERITY]**: ...' shape."""
        self.assertTrue(
            FRS.detect_actionable_findings(_review("**[HIGH SEVERITY]**: the thing is broken\n"))
        )
        self.assertTrue(
            FRS.detect_actionable_findings(_review("**[MEDIUM IMPACT]**: mitigation needed\n"))
        )

    def test_gemini_style_nested_finding(self) -> None:
        """Real shape produced by the Gemini requirements-adversarial reviewer."""
        body = (
            "### 1. Mandatory tracking introduces scope creep\n"
            "\n"
            "**Severity**: MEDIUM\n"
            "**Finding**: The new step...\n"
        )
        self.assertTrue(FRS.detect_actionable_findings(_review(body)))


class ActionableFindingRegexNegativeTests(unittest.TestCase):
    """Prose mentions of severity words must NOT match."""

    def test_prose_mid_sentence(self) -> None:
        self.assertFalse(
            FRS.detect_actionable_findings(
                _review("This would be a HIGH severity issue in production.\n")
            )
        )

    def test_prose_with_severity_word_only(self) -> None:
        self.assertFalse(
            FRS.detect_actionable_findings(
                _review("We consider severity carefully in this review.\n")
            )
        )

    def test_empty_review(self) -> None:
        self.assertFalse(FRS.detect_actionable_findings(_review("")))

    def test_low_severity_is_actionable(self) -> None:
        self.assertTrue(FRS.detect_actionable_findings(_review("- low: minor typo\n")))
        self.assertTrue(FRS.detect_actionable_findings(_review("### LOW: minor typo\n")))
        self.assertTrue(FRS.detect_actionable_findings(_review("**LOW**: typo\n")))

    def test_word_higher_not_match(self) -> None:
        self.assertFalse(
            FRS.detect_actionable_findings(_review("higher-priority item is this\n"))
        )

    def test_severity_word_in_code_block_prose(self) -> None:
        self.assertFalse(
            FRS.detect_actionable_findings(
                _review("For example, the pattern **HIGH severity findings** in docs...\n")
            )
        )

    def test_crlf_line_endings_do_not_break_detection(self) -> None:
        """Windows-style line endings should still detect findings (Gemini coverage F-5)."""
        self.assertTrue(
            FRS.detect_actionable_findings(_review("- HIGH: some finding\r\n"))
        )

    def test_leading_tab_whitespace(self) -> None:
        """Lines with unusual leading whitespace (tabs, mixed) still match."""
        self.assertTrue(FRS.detect_actionable_findings(_review("\t- HIGH: finding\n")))
        self.assertTrue(FRS.detect_actionable_findings(_review("  - HIGH: finding\n")))

    def test_heading_high_availability_is_not_actionable(self) -> None:
        """Spec round-2 Codex edge-cases LOW: '### HIGH availability' is a section title."""
        self.assertFalse(
            FRS.detect_actionable_findings(_review("### HIGH availability target\n"))
        )
        self.assertFalse(
            FRS.detect_actionable_findings(_review("## MEDIUM-term plan\n"))
        )

    def test_fenced_code_block_with_literal_severity_line_is_ignored(self) -> None:
        body = (
            "Here is an example of the pattern:\n"
            "```\n"
            "- HIGH: example finding line for docs\n"
            "```\n"
            "This review itself has no real findings.\n"
        )
        self.assertFalse(FRS.detect_actionable_findings(_review(body)))

    def test_blockquote_and_inline_code_severity_are_ignored(self) -> None:
        body = "> - CRITICAL: quoted example\n\nInline `- HIGH: example` only.\n"
        self.assertFalse(FRS.detect_actionable_findings(_review(body)))

    def test_links_and_image_alt_text_are_ignored(self) -> None:
        body = "[CRITICAL: old note](https://example.com)\n![LOW: alt](image.png)\n"
        self.assertFalse(FRS.detect_actionable_findings(_review(body)))

    def test_auto_accept_note_does_not_self_trigger(self) -> None:
        body = "## Resolution\n- status: accepted\n- note: No HIGH/MEDIUM/LOW/CRITICAL findings detected — auto-accepted\n"
        self.assertFalse(FRS.detect_actionable_findings(_review(body)))

    def test_duplicate_findings_sections_scan_past_first(self) -> None:
        body = "## Findings\n\nNo issues.\n\n## Findings\n\n- CRITICAL: hidden later issue\n"
        self.assertTrue(FRS.detect_actionable_findings(_review(body)))


class ActionableFindingRegexRealReviewTests(unittest.TestCase):
    """The regex must detect findings in THIS feature's own spec reviews."""

    FEATURE_REVIEWS = (
        Path(__file__).resolve().parents[3].parents[1]
        / "feature-runs"
        / "ff-runner-fixes"
        / "reviews"
    )

    def _review_path(self, name: str) -> Path:
        return self.FEATURE_REVIEWS / name

    def test_codex_feasibility_review_is_actionable(self) -> None:
        path = self._review_path("spec.codex.feasibility-adversarial.review.md")
        if not path.exists():
            self.skipTest(f"fixture review missing: {path}")
        self.assertTrue(FRS.detect_actionable_findings(path))

    def test_codex_edge_cases_review_is_actionable(self) -> None:
        path = self._review_path("spec.codex.edge-cases-adversarial.review.md")
        if not path.exists():
            self.skipTest(f"fixture review missing: {path}")
        self.assertTrue(FRS.detect_actionable_findings(path))

    def test_gemini_requirements_review_is_actionable(self) -> None:
        path = self._review_path("spec.gemini.requirements-adversarial.review.md")
        if not path.exists():
            self.skipTest(f"fixture review missing: {path}")
        self.assertTrue(FRS.detect_actionable_findings(path))


class RequiredReviewSelectionTests(unittest.TestCase):
    def test_bundle_2_defaults_only_cover_spec_and_plan(self) -> None:
        self.assertEqual(
            [review["lens"] for review in FRS.required_reviews("spec", False, False, False, [])],
            ["feasibility-adversarial", "requirements-adversarial"],
        )
        self.assertEqual(
            [review["lens"] for review in FRS.required_reviews("plan", False, False, False, [])],
            ["implementation-adversarial", "testability-adversarial"],
        )
        self.assertEqual(FRS.required_reviews("tasks", False, False, False, []), [])
        self.assertEqual(FRS.required_reviews("diff", False, False, False, []), [])
        self.assertEqual(FRS.required_reviews("closeout", False, False, False, []), [])

    def test_diff_with_no_size_info_keeps_no_default_review(self) -> None:
        """Callers that don't size the diff get the historical empty behavior."""
        self.assertEqual(
            FRS.required_reviews("diff", False, False, False, [], diff_changed_lines=None),
            [],
        )

    def test_small_diff_gets_no_default_review(self) -> None:
        self.assertEqual(
            FRS.required_reviews("diff", False, False, False, [], diff_changed_lines=10),
            [],
        )

    def test_substantial_diff_gets_one_gemini_regression_review(self) -> None:
        reviews = FRS.required_reviews("diff", False, False, False, [], diff_changed_lines=200)
        self.assertEqual(
            reviews,
            [
                {
                    "reviewer": "gemini",
                    "lens": "regression-adversarial",
                    "model": FRS.DEFAULT_GEMINI_MODEL,
                },
            ],
        )

    def test_diff_review_triggers_exactly_at_threshold(self) -> None:
        threshold = FRS.DIFF_REVIEW_DEFAULT_MIN_CHANGED_LINES
        below = FRS.required_reviews(
            "diff", False, False, False, [], diff_changed_lines=threshold - 1
        )
        at = FRS.required_reviews(
            "diff", False, False, False, [], diff_changed_lines=threshold
        )
        self.assertEqual(below, [])
        self.assertEqual([r["lens"] for r in at], ["regression-adversarial"])

    def test_custom_diff_review_threshold_overrides_default(self) -> None:
        # A 30-line diff is below the default 50 but above a custom 25.
        self.assertEqual(
            FRS.required_reviews(
                "diff", False, False, False, [], diff_changed_lines=30
            ),
            [],
        )
        reviews = FRS.required_reviews(
            "diff", False, False, False, [], diff_changed_lines=30, diff_review_threshold=25
        )
        self.assertEqual([r["lens"] for r in reviews], ["regression-adversarial"])

    def test_sensitive_diff_review_uses_pro_model(self) -> None:
        reviews = FRS.required_reviews(
            "diff", True, False, False, [], diff_changed_lines=200
        )
        self.assertEqual(reviews[0]["model"], FRS.SENSITIVE_GEMINI_MODEL)

    def test_extra_gemini_lens_not_duplicated_with_gated_diff_review(self) -> None:
        # The size gate already adds regression-adversarial; an explicit
        # --extra-gemini-lens for the same lens must not double it.
        reviews = FRS.required_reviews(
            "diff", False, False, False, ["regression-adversarial"], diff_changed_lines=200
        )
        self.assertEqual([r["lens"] for r in reviews], ["regression-adversarial"])

    def test_extra_gemini_lens_is_appended_on_a_no_default_stage(self) -> None:
        reviews = FRS.required_reviews("tasks", False, False, False, ["coverage-adversarial"])
        self.assertEqual(
            reviews,
            [
                {
                    "reviewer": "gemini",
                    "lens": "coverage-adversarial",
                    "model": FRS.DEFAULT_GEMINI_MODEL,
                },
            ],
        )


class CountChangedDiffLinesTests(unittest.TestCase):
    """Tests for the count_changed_diff_lines helper used by the diff-review gate."""

    def test_empty_diff_is_zero(self) -> None:
        self.assertEqual(FRS.count_changed_diff_lines(""), 0)

    def test_counts_added_and_removed_content_lines(self) -> None:
        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,3 +1,4 @@\n"
            " context line\n"
            "-old line\n"
            "+new line one\n"
            "+new line two\n"
        )
        # 1 removed + 2 added = 3 changed; headers, hunk, and context excluded.
        self.assertEqual(FRS.count_changed_diff_lines(diff), 3)

    def test_file_headers_are_not_counted(self) -> None:
        diff = "--- a/x\n+++ b/x\n"
        self.assertEqual(FRS.count_changed_diff_lines(diff), 0)

    def test_hunk_header_and_context_not_counted(self) -> None:
        diff = "@@ -1 +1 @@\n unchanged\n"
        self.assertEqual(FRS.count_changed_diff_lines(diff), 0)


class ActionableFindingShapesManifestTests(unittest.TestCase):
    def test_shapes_manifest_present(self) -> None:
        """ACTIONABLE_FINDING_SHAPES documents every pattern the regex covers."""
        self.assertIsInstance(FRS.ACTIONABLE_FINDING_SHAPES, tuple)
        self.assertGreaterEqual(len(FRS.ACTIONABLE_FINDING_SHAPES), 10)


class CountFindingsBySeverityTests(unittest.TestCase):
    """Tests for the _count_findings_by_severity helper."""

    def _scan(self, text: str) -> dict[str, int]:
        """Run the same pre-processing pipeline as _print_findings_summary."""
        processed = FRS._strip_non_finding_markdown(FRS._findings_scan_text(text)).lower()
        return FRS._count_findings_by_severity(processed)

    def test_counts_each_severity_correctly(self) -> None:
        body = (
            "- critical: data loss risk\n"
            "- high: missing index\n"
            "- medium: stale cache\n"
            "- low: minor typo\n"
        )
        counts = self._scan(body)
        self.assertEqual(counts["CRITICAL"], 1)
        self.assertEqual(counts["HIGH"], 1)
        self.assertEqual(counts["MEDIUM"], 1)
        self.assertEqual(counts["LOW"], 1)

    def test_returns_zero_for_absent_severities(self) -> None:
        body = "- high: only one finding\n"
        counts = self._scan(body)
        self.assertEqual(counts["CRITICAL"], 0)
        self.assertEqual(counts["MEDIUM"], 0)
        self.assertEqual(counts["LOW"], 0)

    def test_empty_file_returns_all_zeros(self) -> None:
        counts = self._scan("")
        self.assertEqual(counts, {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0})

    def test_prose_mention_of_high_without_finding_shape_not_counted(self) -> None:
        body = "This would be a HIGH severity issue in production.\n"
        counts = self._scan(body)
        self.assertEqual(counts["HIGH"], 0)

    def test_multiple_high_findings_are_counted_correctly(self) -> None:
        body = (
            "- high: first issue\n"
            "- high: second issue\n"
            "- medium: one medium\n"
        )
        counts = self._scan(body)
        self.assertEqual(counts["HIGH"], 2)
        self.assertEqual(counts["MEDIUM"], 1)

    def test_fenced_code_block_not_counted(self) -> None:
        body = (
            "Example:\n"
            "```\n"
            "- HIGH: example finding\n"
            "```\n"
            "No real findings here.\n"
        )
        counts = self._scan(body)
        self.assertEqual(counts["HIGH"], 0)

    def test_inline_severity_field_form_counted(self) -> None:
        body = "**Severity**: HIGH\nsome detail\n"
        counts = self._scan(body)
        self.assertEqual(counts["HIGH"], 1)


if __name__ == "__main__":
    unittest.main()
