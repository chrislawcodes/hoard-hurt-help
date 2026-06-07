"""Runtime [P:] safety: parallel workers must write disjoint file sets.

[P:] annotations are validated for disjoint scopes at declaration time, but a
Codex worker can write outside its declared scope at runtime. _detect_parallel_
file_overlap catches that before cherry-pick so it fails loudly instead of
producing a confusing conflict (or silently merging two unrelated edits).
"""
from __future__ import annotations

import unittest

import factory_cmd_implement as IMP


class DetectParallelFileOverlapTests(unittest.TestCase):
    def test_disjoint_sets_have_no_overlap(self) -> None:
        files = {0: {"app/a.py", "app/b.py"}, 1: {"app/c.py"}}
        self.assertIsNone(IMP._detect_parallel_file_overlap(files))

    def test_shared_file_is_flagged_with_both_task_indexes(self) -> None:
        files = {0: {"app/a.py", "app/shared.py"}, 1: {"app/shared.py"}}
        msg = IMP._detect_parallel_file_overlap(files)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("app/shared.py", msg)
        self.assertIn("0", msg)
        self.assertIn("1", msg)

    def test_protected_files_are_excluded(self) -> None:
        # Protected files (CLAUDE.md, .gitignore, …) are reverted after every
        # worker, so a shared touch of one is not a real overlap.
        protected = IMP.PROTECTED_FILES[0]
        files = {0: {protected, "app/a.py"}, 1: {protected, "app/b.py"}}
        self.assertIsNone(IMP._detect_parallel_file_overlap(files))

    def test_same_task_repeating_a_file_is_not_overlap(self) -> None:
        files = {0: {"app/a.py"}}
        self.assertIsNone(IMP._detect_parallel_file_overlap(files))

    def test_empty_input(self) -> None:
        self.assertIsNone(IMP._detect_parallel_file_overlap({}))


if __name__ == "__main__":
    unittest.main()
