"""Tests for the [CHECKPOINT] marker regex / parser in factory_stages.

The marker may appear on a markdown heading or a list item. It must NOT match a
bare ``[CHECKPOINT]`` that appears mid-prose. The parser hashes only the matched
lines, so heading-form markers must be detected for real runs to checkpoint.

The scripts dir is put on sys.path by the package conftest.py.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import factory_stages as FS


class CheckpointMarkerRegexTests(unittest.TestCase):
    def _matches(self, text: str) -> list[str]:
        return FS._CHECKPOINT_MARKER_RE.findall(text)

    def test_heading_form_is_detected(self) -> None:
        text = "### [CHECKPOINT] Slice 1 — Foo\n"
        self.assertEqual(self._matches(text), ["### [CHECKPOINT] Slice 1 — Foo"])

    def test_heading_levels_one_through_six(self) -> None:
        for hashes in ("#", "##", "###", "####", "#####", "######"):
            line = f"{hashes} [CHECKPOINT] Slice"
            self.assertEqual(self._matches(line + "\n"), [line], hashes)

    def test_list_item_marker_at_end_still_matches(self) -> None:
        text = "- end of CP1 [CHECKPOINT]\n"
        self.assertEqual(self._matches(text), ["- end of CP1 [CHECKPOINT]"])

    def test_ordered_and_checkbox_list_items_match(self) -> None:
        text = (
            "1. do a thing [CHECKPOINT]\n"
            "- [ ] open box [CHECKPOINT]\n"
            "- [x] done box [CHECKPOINT]\n"
        )
        self.assertEqual(len(self._matches(text)), 3)

    def test_mid_prose_marker_is_not_matched(self) -> None:
        text = "This sentence mentions [CHECKPOINT] in the middle of prose.\n"
        self.assertEqual(self._matches(text), [])

    def test_seven_hashes_is_not_a_heading(self) -> None:
        # ####### is not a valid markdown heading (1-6 only).
        text = "####### [CHECKPOINT] not a heading\n"
        self.assertEqual(self._matches(text), [])

    # ── Regression: real-world forms that produced "0 markers" incidents ──

    def test_dedup_engine_cseries_heading_form(self) -> None:
        # dedup-engine-cseries run: "### [CHECKPOINT] Slice 1 …" was not
        # recognized → "no markers — covering full branch".
        line = "### [CHECKPOINT] Slice 1 — extract the dedup engine"
        self.assertEqual(self._matches(line + "\n"), [line])

    def test_user_roles_bold_verify_line_form(self) -> None:
        # user-roles run: markers were placed on bold "**Verify:**" lines →
        # 0 detected → all 5 slices collapsed into one Codex dispatch.
        line = "**Verify:** role checks enforced on every route [CHECKPOINT]"
        self.assertEqual(self._matches(line + "\n"), [line])

    def test_bold_wrapped_marker_on_list_item(self) -> None:
        line = "- end of slice 1 **[CHECKPOINT]**"
        self.assertEqual(self._matches(line + "\n"), [line])

    def test_bold_wrapped_marker_on_checkbox_item(self) -> None:
        line = "- [ ] verify tests pass **[CHECKPOINT]**"
        self.assertEqual(self._matches(line + "\n"), [line])

    def test_bold_heading_variants(self) -> None:
        for line in (
            "### **[CHECKPOINT] Slice 2 — Bar**",
            "### **[CHECKPOINT]** Slice 2 — Bar",
        ):
            self.assertEqual(self._matches(line + "\n"), [line], line)

    def test_fully_bold_line_form(self) -> None:
        line = "**Verify: tests green [CHECKPOINT]**"
        self.assertEqual(self._matches(line + "\n"), [line])

    def test_bold_led_line_not_ending_in_marker_is_not_matched(self) -> None:
        # Bold-led lines follow the list-item discipline: the marker must END
        # the line, so a mid-sentence mention is not a slice boundary.
        text = "**Note** we will hit [CHECKPOINT] later in this slice\n"
        self.assertEqual(self._matches(text), [])


class MaskFencedCodeBlocksTests(unittest.TestCase):
    def test_marker_inside_backtick_fence_is_masked(self) -> None:
        text = (
            "- [ ] T1 do work\n"
            "```markdown\n"
            "- example boundary [CHECKPOINT]\n"
            "```\n"
            "- real boundary [CHECKPOINT]\n"
        )
        masked = FS.mask_fenced_code_blocks(text)
        self.assertEqual(FS._CHECKPOINT_MARKER_RE.findall(masked), ["- real boundary [CHECKPOINT]"])

    def test_marker_inside_tilde_fence_is_masked(self) -> None:
        text = "~~~\n### [CHECKPOINT] Slice 1\n~~~\n"
        self.assertEqual(FS._CHECKPOINT_MARKER_RE.findall(FS.mask_fenced_code_blocks(text)), [])

    def test_line_count_and_non_fence_lines_preserved(self) -> None:
        text = "a\n```\nb\n```\nc"
        masked = FS.mask_fenced_code_blocks(text)
        self.assertEqual(masked.splitlines(), ["a", "", "", "", "c"])

    def test_unclosed_fence_masks_to_end_of_file(self) -> None:
        text = "```\n- inside forever [CHECKPOINT]\n- still inside [CHECKPOINT]\n"
        self.assertEqual(FS._CHECKPOINT_MARKER_RE.findall(FS.mask_fenced_code_blocks(text)), [])

    def test_closing_fence_must_match_char_and_length(self) -> None:
        # A shorter or different-char sequence does not close the fence.
        text = (
            "````\n"
            "```\n"
            "- not a boundary [CHECKPOINT]\n"
            "````\n"
            "- real [CHECKPOINT]\n"
        )
        masked = FS.mask_fenced_code_blocks(text)
        self.assertEqual(FS._CHECKPOINT_MARKER_RE.findall(masked), ["- real [CHECKPOINT]"])


class ParseCheckpointMarkersTests(unittest.TestCase):
    def _parse(self, tasks_text: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "tasks.md").write_text(tasks_text, encoding="utf-8")
            with patch.object(FS, "workflow_dir", return_value=run_dir):
                return FS.parse_checkpoint_markers("slug")

    def test_heading_form_counts_and_hashes(self) -> None:
        text = (
            "# Tasks\n\n"
            "### [CHECKPOINT] Slice 1 — Foo\n"
            "- [ ] T1 do work\n"
            "### [CHECKPOINT] Slice 2 — Bar\n"
        )
        count, sha = self._parse(text)
        self.assertEqual(count, 2)
        self.assertTrue(sha)

    def test_mid_prose_marker_does_not_count(self) -> None:
        text = "# Tasks\n\nWe will hit a [CHECKPOINT] later in this paragraph.\n"
        count, sha = self._parse(text)
        self.assertEqual(count, 0)
        self.assertEqual(sha, "")

    def test_marker_inside_code_fence_does_not_count(self) -> None:
        text = (
            "# Tasks\n\n"
            "### [CHECKPOINT] Slice 1 — Foo\n"
            "- [ ] T1 document the marker syntax\n"
            "```markdown\n"
            "### [CHECKPOINT] example, not a boundary\n"
            "- also an example [CHECKPOINT]\n"
            "```\n"
        )
        count, _sha = self._parse(text)
        self.assertEqual(count, 1)

    def test_user_roles_bold_verify_markers_all_count(self) -> None:
        # Regression: the user-roles tasks.md used bold "**Verify:**" marker
        # lines for all 5 slices and the engine detected 0.
        slices = "\n".join(
            f"- [ ] T{i} build part {i}\n**Verify:** part {i} works [CHECKPOINT]"
            for i in range(1, 6)
        )
        count, sha = self._parse(f"# Tasks\n\n{slices}\n")
        self.assertEqual(count, 5)
        self.assertTrue(sha)


if __name__ == "__main__":
    unittest.main()
