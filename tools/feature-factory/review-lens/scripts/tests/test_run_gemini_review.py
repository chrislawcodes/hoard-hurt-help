"""Unit tests for run_gemini_review.expand_new_files_in_diff.

Tests the prompt-construction helper that replaces new-file diff chunks
with full file content to avoid false positives from Gemini misreading
`+`-prefixed lines as partial context.

Covers:
  a. New file → prompt block contains "## New file:" with full content, no + prefixes.
  b. Modified file → diff format unchanged.
  c. Mixed (one new + one modified) → both formats appear, correctly separated.
"""
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import run_gemini_review as RUN_GEMINI  # noqa: E402

expand_new_files_in_diff = RUN_GEMINI.expand_new_files_in_diff


# ---------------------------------------------------------------------------
# Sample diff fixtures
# ---------------------------------------------------------------------------

_MODIFIED_FILE_DIFF = """\
diff --git a/cloud/apps/web/src/components/Foo.tsx b/cloud/apps/web/src/components/Foo.tsx
index abc1234..def5678 100644
--- a/cloud/apps/web/src/components/Foo.tsx
+++ b/cloud/apps/web/src/components/Foo.tsx
@@ -1,5 +1,6 @@
 import React from 'react';
+import { useState } from 'react';

 export function Foo() {
-  return <div>old</div>;
+  return <div>new</div>;
 }
"""

_NEW_FILE_DIFF_HEADER = """\
diff --git a/cloud/apps/web/src/components/Bar.tsx b/cloud/apps/web/src/components/Bar.tsx
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/cloud/apps/web/src/components/Bar.tsx
@@ -0,0 +1,6 @@
+import React from 'react';
+import { MyType } from './types';
+
+export function Bar({ value }: { value: MyType }) {{
+  return <div>{{value}}</div>;
+}}
"""

_NEW_FILE_CONTENT = """\
import React from 'react';
import { MyType } from './types';

export function Bar({ value }: { value: MyType }) {
  return <div>{value}</div>;
}
"""


class ExpandNewFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_root = Path(self._tmpdir.name)

    def _write_file(self, rel: str, content: str) -> Path:
        p = self.repo_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_new_file_replaced_with_full_content(self) -> None:
        """a. New file diff chunk → replaced with ## New file: header and raw content."""
        # Write the actual file to disk
        self._write_file("cloud/apps/web/src/components/Bar.tsx", _NEW_FILE_CONTENT)

        result = expand_new_files_in_diff(
            _NEW_FILE_DIFF_HEADER,
            self.repo_root,
            max_total_chars=500000,
        )

        # Should contain the header marker
        self.assertIn("## New file: cloud/apps/web/src/components/Bar.tsx", result)
        # Should contain the actual file content (not prefixed with +)
        self.assertIn("import { MyType }", result)
        # Should NOT contain the git diff +++ prefix lines for the new file
        self.assertNotIn("+++ b/", result)
        # Should NOT contain lines like "+import React" (git diff prefix)
        lines_with_plus_prefix = [l for l in result.splitlines() if l.startswith("+import")]
        self.assertEqual(lines_with_plus_prefix, [], "Should not have +-prefixed import lines")

    def test_modified_file_diff_unchanged(self) -> None:
        """b. Modified file diff chunk → kept in original diff format."""
        result = expand_new_files_in_diff(
            _MODIFIED_FILE_DIFF,
            self.repo_root,
            max_total_chars=500000,
        )

        # The modified file diff should be preserved
        self.assertIn("--- a/cloud/apps/web/src/components/Foo.tsx", result)
        self.assertIn("+++ b/cloud/apps/web/src/components/Foo.tsx", result)
        self.assertIn("+import { useState }", result)
        # No new-file header for modified file
        self.assertNotIn("## New file:", result)

    def test_mixed_new_and_modified_both_present(self) -> None:
        """c. Mixed diff (new + modified) → both formats appear correctly."""
        self._write_file("cloud/apps/web/src/components/Bar.tsx", _NEW_FILE_CONTENT)

        # Combine both diffs
        mixed_diff = _MODIFIED_FILE_DIFF + "\n" + _NEW_FILE_DIFF_HEADER

        result = expand_new_files_in_diff(
            mixed_diff,
            self.repo_root,
            max_total_chars=500000,
        )

        # Modified file: original diff format preserved
        self.assertIn("+++ b/cloud/apps/web/src/components/Foo.tsx", result)
        self.assertIn("+import { useState }", result)

        # New file: replaced with full content under header
        self.assertIn("## New file: cloud/apps/web/src/components/Bar.tsx", result)
        self.assertIn("import { MyType }", result)

    def test_new_file_not_on_disk_keeps_original_diff(self) -> None:
        """If the new file does not exist on disk, keep the original diff chunk."""
        result = expand_new_files_in_diff(
            _NEW_FILE_DIFF_HEADER,
            self.repo_root,  # file does NOT exist in tmpdir
            max_total_chars=500000,
        )

        # Should keep the original diff
        self.assertIn("+++ b/cloud/apps/web/src/components/Bar.tsx", result)
        # Should NOT have the ## New file: header
        self.assertNotIn("## New file:", result)

    def test_max_total_chars_limits_output(self) -> None:
        """Expansion honours max_total_chars cap."""
        self._write_file("cloud/apps/web/src/components/Bar.tsx", _NEW_FILE_CONTENT)

        result = expand_new_files_in_diff(
            _NEW_FILE_DIFF_HEADER,
            self.repo_root,
            max_total_chars=50,
        )

        self.assertLessEqual(len(result), 50)


prompt_for = RUN_GEMINI.prompt_for


class PromptForFailLoudTests(unittest.TestCase):
    """The fail-loud swallow check is included for code stages only."""

    _NEEDLE = "swallowed errors and silent"

    def test_diff_stage_includes_fail_loud_check(self) -> None:
        prompt = prompt_for("diff", "correctness-adversarial", "diff", "some code", [])
        self.assertIn(self._NEEDLE, prompt)

    def test_closeout_stage_includes_fail_loud_check(self) -> None:
        prompt = prompt_for("closeout", "regression-adversarial", "diff", "some code", [])
        self.assertIn(self._NEEDLE, prompt)

    def test_artifact_stages_omit_fail_loud_check(self) -> None:
        for stage in ("spec", "plan", "tasks"):
            prompt = prompt_for(stage, "requirements-adversarial", "spec.md", "text", [])
            self.assertNotIn(self._NEEDLE, prompt)


class PromptForFindingsFirstTests(unittest.TestCase):
    """betrayal-8-4 Change 1: reviewers must reach the required findings format
    before writing extended analysis, so a response cut off mid-turn still
    contains parseable findings instead of dying mid-prose.
    """

    _NEEDLE = "Emit the required structured output first"

    def test_instruction_present_for_code_stage(self) -> None:
        prompt = prompt_for("diff", "correctness-adversarial", "diff", "some code", [])
        self.assertIn(self._NEEDLE, prompt)

    def test_instruction_present_for_artifact_stage(self) -> None:
        prompt = prompt_for("spec", "requirements-adversarial", "spec.md", "text", [])
        self.assertIn(self._NEEDLE, prompt)

    def test_instruction_precedes_the_format_contract(self) -> None:
        # Must land before "Return markdown using exactly these sections" —
        # it has to shape how the reviewer approaches the whole response, not
        # read as an afterthought once the sections are already spelled out.
        prompt = prompt_for("plan", "feasibility-adversarial", "plan.md", "text", [])
        self.assertLess(
            prompt.index(self._NEEDLE),
            prompt.index("Return markdown using exactly these sections:"),
        )


class PromptForCompletenessLensTests(unittest.TestCase):
    """betrayal-8-4 Change 2: completeness-adversarial is a named lens with its
    own tracing instruction; lenses without an entry stay pure interpolation.
    """

    _NEEDLE = "Trace every consumer and render path"

    def test_completeness_adversarial_gets_the_tracing_instruction(self) -> None:
        prompt = prompt_for("diff", "completeness-adversarial", "diff", "some code", [])
        self.assertIn(self._NEEDLE, prompt)
        self.assertIn("a value changed in one place and stale in another", prompt)

    def test_unrelated_lens_does_not_get_the_completeness_instruction(self) -> None:
        prompt = prompt_for("diff", "correctness-adversarial", "diff", "some code", [])
        self.assertNotIn(self._NEEDLE, prompt)

    def test_unmapped_lens_name_still_interpolates_cleanly(self) -> None:
        # Lenses with no LENS_INSTRUCTIONS entry are still pure free-form
        # interpolation into "using a {lens} lens" — the fallback every other
        # lens name relies on.
        prompt = prompt_for("spec", "some-freeform-lens", "spec.md", "text", [])
        self.assertIn("using a some-freeform-lens lens", prompt)
        self.assertNotIn(self._NEEDLE, prompt)


if __name__ == "__main__":
    unittest.main()
