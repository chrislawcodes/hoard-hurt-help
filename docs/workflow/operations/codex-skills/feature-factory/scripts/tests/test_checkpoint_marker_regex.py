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


if __name__ == "__main__":
    unittest.main()
