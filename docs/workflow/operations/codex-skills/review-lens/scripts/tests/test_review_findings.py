"""Tests for the structured review-findings contract (review_findings.py).

Covers the fail-closed classification matrix:
  - valid JSON block with findings        → JSON is the source of truth
  - affirmative clean JSON                → clean (auto-accept allowed)
  - malformed JSON block                  → UNPARSEABLE (never falls to regex)
  - no JSON + legacy prose shapes         → regex fallback works
  - no JSON + nothing + non-trivial body  → UNPARSEABLE (not auto-accept)
  - no JSON + trivial body                → legacy clean behavior
"""
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]  # review-lens/scripts
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import review_findings as RF  # noqa: E402


CLEAN_BLOCK = '```json\n{"reviewed": true, "findings": []}\n```\n'

FINDINGS_BLOCK = (
    "```json\n"
    '{"reviewed": true, "findings": ['
    '{"severity": "HIGH", "title": "swallowed error", "detail": "except returns None"}, '
    '{"severity": "medium", "title": "stale cache", "detail": ""}'
    "]}\n"
    "```\n"
)

# > 400 chars of prose that matches no legacy shape and names no severity word.
NON_TRIVIAL_PROSE = (
    "The retry logic in the connector appears to have a subtle flaw where the "
    "backoff window is computed from the wrong timestamp, which could cause a "
    "storm of requests after a deploy. Additionally the pagination cursor is "
    "not persisted between polls, so a restart may replay turns that were "
    "already acknowledged. Both of these deserve a close look before merge, "
    "and the second one in particular could corrupt the standings table if two "
    "workers race on the same match id during the replay window.\n"
)


class ParseFindingsJsonTests(unittest.TestCase):
    def test_valid_block_with_findings(self) -> None:
        result = RF.parse_findings_json("## Findings\n\nprose\n\n" + FINDINGS_BLOCK)
        self.assertEqual(result.status, RF.JSON_BLOCK_VALID)
        self.assertEqual(len(result.findings), 2)
        self.assertEqual(result.findings[0].severity, "HIGH")
        self.assertEqual(result.findings[0].title, "swallowed error")
        # Severity is normalized to uppercase.
        self.assertEqual(result.findings[1].severity, "MEDIUM")

    def test_affirmative_clean_block(self) -> None:
        result = RF.parse_findings_json("## Findings\n\nNo issues.\n\n" + CLEAN_BLOCK)
        self.assertEqual(result.status, RF.JSON_BLOCK_VALID)
        self.assertEqual(result.findings, ())

    def test_absent_when_no_fenced_block(self) -> None:
        result = RF.parse_findings_json("## Findings\n\n- high: something\n")
        self.assertEqual(result.status, RF.JSON_BLOCK_ABSENT)

    def test_fenced_code_without_reviewed_marker_is_ignored(self) -> None:
        text = '```json\n{"config": {"retries": 3}}\n```\n'
        self.assertEqual(RF.parse_findings_json(text).status, RF.JSON_BLOCK_ABSENT)

    def test_invalid_json_is_malformed(self) -> None:
        text = '```json\n{"reviewed": true, "findings": [}\n```\n'
        result = RF.parse_findings_json(text)
        self.assertEqual(result.status, RF.JSON_BLOCK_MALFORMED)
        self.assertIn("invalid JSON", result.error)

    def test_reviewed_false_is_malformed(self) -> None:
        text = '```json\n{"reviewed": false, "findings": []}\n```\n'
        result = RF.parse_findings_json(text)
        self.assertEqual(result.status, RF.JSON_BLOCK_MALFORMED)
        self.assertIn("reviewed", result.error)

    def test_missing_findings_key_is_malformed(self) -> None:
        text = '```json\n{"reviewed": true}\n```\n'
        self.assertEqual(RF.parse_findings_json(text).status, RF.JSON_BLOCK_MALFORMED)

    def test_findings_not_a_list_is_malformed(self) -> None:
        text = '```json\n{"reviewed": true, "findings": "none"}\n```\n'
        self.assertEqual(RF.parse_findings_json(text).status, RF.JSON_BLOCK_MALFORMED)

    def test_unknown_severity_is_malformed(self) -> None:
        text = (
            "```json\n"
            '{"reviewed": true, "findings": [{"severity": "BLOCKER", "title": "x"}]}\n'
            "```\n"
        )
        result = RF.parse_findings_json(text)
        self.assertEqual(result.status, RF.JSON_BLOCK_MALFORMED)
        self.assertIn("severity", result.error)

    def test_empty_title_is_malformed(self) -> None:
        text = (
            "```json\n"
            '{"reviewed": true, "findings": [{"severity": "LOW", "title": "  "}]}\n'
            "```\n"
        )
        self.assertEqual(RF.parse_findings_json(text).status, RF.JSON_BLOCK_MALFORMED)

    def test_non_string_detail_is_malformed(self) -> None:
        text = (
            "```json\n"
            '{"reviewed": true, "findings": [{"severity": "LOW", "title": "x", "detail": 3}]}\n'
            "```\n"
        )
        self.assertEqual(RF.parse_findings_json(text).status, RF.JSON_BLOCK_MALFORMED)

    def test_missing_detail_defaults_to_empty(self) -> None:
        text = (
            "```json\n"
            '{"reviewed": true, "findings": [{"severity": "LOW", "title": "typo"}]}\n'
            "```\n"
        )
        result = RF.parse_findings_json(text)
        self.assertEqual(result.status, RF.JSON_BLOCK_VALID)
        self.assertEqual(result.findings[0].detail, "")

    def test_unclosed_fence_with_marker_is_malformed(self) -> None:
        text = '## Findings\n\nprose\n\n```json\n{"reviewed": true, "findings": []}\n'
        result = RF.parse_findings_json(text)
        self.assertEqual(result.status, RF.JSON_BLOCK_MALFORMED)
        self.assertIn("never closed", result.error)

    def test_last_candidate_wins_over_quoted_example(self) -> None:
        # The reviewer quoted the clean example in prose, then ended with the
        # real block. The LAST candidate is the contract.
        text = (
            "## Findings\n\nThe template block looks like:\n\n"
            + CLEAN_BLOCK
            + "\nBut I did find issues.\n\n"
            + FINDINGS_BLOCK
        )
        result = RF.parse_findings_json(text)
        self.assertEqual(result.status, RF.JSON_BLOCK_VALID)
        self.assertEqual(len(result.findings), 2)

    def test_malformed_last_candidate_does_not_fall_back_to_earlier_valid(self) -> None:
        text = (
            CLEAN_BLOCK
            + "\n```json\n{\"reviewed\": true, \"findings\": [oops]}\n```\n"
        )
        self.assertEqual(RF.parse_findings_json(text).status, RF.JSON_BLOCK_MALFORMED)


class ClassifyReviewTextTests(unittest.TestCase):
    def test_valid_json_with_findings_is_source_of_truth(self) -> None:
        # Prose contains no regex-recognizable shape; JSON still counts them.
        text = "## Findings\n\nTwo problems, described loosely.\n\n" + FINDINGS_BLOCK
        c = RF.classify_review_text(text)
        self.assertEqual(c.source, RF.FINDINGS_SOURCE_JSON)
        self.assertEqual(c.counts["HIGH"], 1)
        self.assertEqual(c.counts["MEDIUM"], 1)
        self.assertTrue(c.has_findings)
        self.assertFalse(c.is_unparseable)

    def test_affirmative_clean_json_beats_non_trivial_body(self) -> None:
        # A long clean review WITH the affirmative block must NOT be
        # unparseable — the clean bill is the whole point of the contract.
        text = "## Findings\n\n" + NON_TRIVIAL_PROSE + "\n" + CLEAN_BLOCK
        c = RF.classify_review_text(text)
        self.assertEqual(c.source, RF.FINDINGS_SOURCE_JSON)
        self.assertFalse(c.has_findings)
        self.assertFalse(c.is_unparseable)

    def test_json_counts_override_prose_regex_counts(self) -> None:
        # Prose shows 3 legacy-shaped findings; JSON records 1. JSON wins.
        text = (
            "## Findings\n\n- high: a\n- high: b\n- medium: c\n\n"
            "```json\n"
            '{"reviewed": true, "findings": [{"severity": "LOW", "title": "only one"}]}\n'
            "```\n"
        )
        c = RF.classify_review_text(text)
        self.assertEqual(c.source, RF.FINDINGS_SOURCE_JSON)
        self.assertEqual(c.counts, {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 1})

    def test_malformed_json_is_unparseable_even_with_regex_findings(self) -> None:
        # A broken block never silently falls through to the regex.
        text = (
            "## Findings\n\n- high: real finding\n\n"
            '```json\n{"reviewed": true, "findings": [broken]}\n```\n'
        )
        c = RF.classify_review_text(text)
        self.assertTrue(c.is_unparseable)
        self.assertIn("malformed", c.detail)

    def test_no_json_legacy_shapes_fall_back_to_regex(self) -> None:
        shapes = (
            "- high: missing index\n",
            "- **HIGH**: path handler missing\n",
            "| **CRITICAL** | some text |\n",
            "1. MEDIUM: something is wrong\n",
            "### HIGH: missing index\n",
            "**Severity**: HIGH\n",
        )
        for shape in shapes:
            c = RF.classify_review_text("## Findings\n\n" + shape)
            self.assertEqual(c.source, RF.FINDINGS_SOURCE_LEGACY, shape)
            self.assertTrue(c.has_findings, shape)

    def test_no_json_no_regex_non_trivial_body_is_unparseable(self) -> None:
        c = RF.classify_review_text("## Findings\n\n" + NON_TRIVIAL_PROSE)
        self.assertTrue(c.is_unparseable)
        self.assertFalse(c.has_findings)
        self.assertIn("cannot be proven clean", c.detail)

    def test_no_json_trivial_body_keeps_legacy_clean_behavior(self) -> None:
        c = RF.classify_review_text("## Findings\n\nNo findings returned.\n")
        self.assertEqual(c.source, RF.FINDINGS_SOURCE_LEGACY)
        self.assertFalse(c.has_findings)
        self.assertFalse(c.is_unparseable)

    def test_frontmatter_does_not_count_toward_body_size(self) -> None:
        frontmatter = (
            "---\n"
            'reviewer: "codex"\n'
            'lens: "feasibility-adversarial"\n'
            'stage: "spec"\n'
            'artifact_path: "docs/workflow/feature-runs/demo/spec.md"\n'
            'artifact_sha256: "' + "a" * 64 + '"\n'
            'repo_root: "."\n'
            'git_head_sha: "' + "b" * 40 + '"\n'
            'git_base_ref: "origin/main"\n'
            'git_base_sha: "' + "c" * 40 + '"\n'
            'resolution_status: "open"\n'
            'resolution_note: ""\n'
            'raw_output_path: "x.raw.txt"\n'
            "---\n\n"
        )
        text = frontmatter + "# Review: spec lens\n\n## Findings\n\nNo findings returned.\n"
        c = RF.classify_review_text(text)
        self.assertEqual(c.source, RF.FINDINGS_SOURCE_LEGACY)
        self.assertFalse(c.is_unparseable)

    def test_runner_boilerplate_sections_do_not_count_toward_body_size(self) -> None:
        boilerplate = (
            "## Token Stats\n\n" + ("- model-x: input=123456, output=98765\n" * 12)
            + "\n## Resolution\n- status: open\n- note: "
            + ("a really long operator note " * 20) + "\n"
        )
        text = "## Findings\n\nNo findings returned.\n\n## Residual Risks\n\n- None.\n\n" + boilerplate
        c = RF.classify_review_text(text)
        self.assertEqual(c.source, RF.FINDINGS_SOURCE_LEGACY)
        self.assertFalse(c.is_unparseable)

    def test_unknown_sections_count_toward_body_size(self) -> None:
        # Reviewer-authored custom sections are part of the review body: a
        # non-trivial review can't dodge the fail-closed rule by using its own
        # section heading.
        text = "## Findings\n\nSee analysis.\n\n## Analysis\n\n" + NON_TRIVIAL_PROSE
        self.assertTrue(RF.classify_review_text(text).is_unparseable)


class PromptContractTests(unittest.TestCase):
    def test_contract_lines_include_schema_and_clean_example(self) -> None:
        joined = "\n".join(RF.FINDINGS_JSON_CONTRACT_LINES)
        self.assertIn('"reviewed": true', joined)
        self.assertIn('"severity"', joined)
        self.assertIn(RF.FINDINGS_JSON_CLEAN_EXAMPLE, joined)
        self.assertIn("CRITICAL, HIGH, MEDIUM, LOW", joined)

    def test_clean_example_round_trips_through_parser(self) -> None:
        result = RF.parse_findings_json("```json\n" + RF.FINDINGS_JSON_CLEAN_EXAMPLE + "\n```\n")
        self.assertEqual(result.status, RF.JSON_BLOCK_VALID)
        self.assertEqual(result.findings, ())


if __name__ == "__main__":
    unittest.main()
