"""Tests for the proposed-fixes tracker (postmortem → recurring-fix log).

Closeout parses the run postmortem's "Proposed workflow changes" section and
appends each proposal to proposed-fixes.md as ``- [<slug>, <date>] <one-liner>``
so recurring proposals become visible (2+ runs → must become a tracked fix).
Exact-duplicate lines are skipped.

Section-shape fixtures mirror the real postmortems on disk
(strategy-first-onboarding: numbered+bold; user-roles: dashed+bold with a
different heading parenthetical).
"""
import argparse
import contextlib
import gc
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_state as FACTORY_STATE  # noqa: E402
import factory_cmd_closeout as FACTORY_CMD_CLOSEOUT  # noqa: E402

_extract_proposed_changes = FACTORY_CMD_CLOSEOUT._extract_proposed_changes
_sync_proposed_fixes = FACTORY_CMD_CLOSEOUT._sync_proposed_fixes


NUMBERED_POSTMORTEM = """# Postmortem: strategy-first-onboarding

## What happened

Stuff.

## Proposed workflow changes (for human approval)

1. **Run the full suite, not just slice tests, before declaring a slice done** —
   or at minimum run the full Preflight Gate after the *first* slice that changes
   a shared route.
2. **Count `[CHECKPOINT]` markers only on their own line** (e.g. lines matching
   a strict pattern), so prose mentions don't inflate the slice count.
3. **Make `deliver --create-pr` push the branch first** (and warn/rebase when
   behind) instead of failing on a missing remote ref.

## Next section

Not proposals.
"""

DASHED_POSTMORTEM = """# Postmortem: user-roles

## Proposed workflow changes (require human approval)

- **[engine] Fix the diff-stage expansion-hash mismatch** (separate session) —
  hash and validate the same artifact. Add a regression test.
- **[engine] Make the codex review timeout configurable end-to-end** — thread a
  flag from `checkpoint` down (parity with the gemini flag); raise the default.
"""

NO_BOLD_POSTMORTEM = """# Postmortem

## Proposed workflow changes

- add a retry to the flaky sync step so one transient network error does not
  fail the whole checkpoint
"""


class ExtractProposedChangesTests(unittest.TestCase):
    def test_extracts_numbered_bold_items(self) -> None:
        proposals = _extract_proposed_changes(NUMBERED_POSTMORTEM)
        self.assertEqual(
            proposals,
            [
                "Run the full suite, not just slice tests, before declaring a slice done",
                "Count `[CHECKPOINT]` markers only on their own line",
                "Make `deliver --create-pr` push the branch first",
            ],
        )

    def test_extracts_dashed_bold_items_with_alternate_heading(self) -> None:
        proposals = _extract_proposed_changes(DASHED_POSTMORTEM)
        self.assertEqual(
            proposals,
            [
                "[engine] Fix the diff-stage expansion-hash mismatch",
                "[engine] Make the codex review timeout configurable end-to-end",
            ],
        )

    def test_item_without_bold_uses_collapsed_text(self) -> None:
        proposals = _extract_proposed_changes(NO_BOLD_POSTMORTEM)
        self.assertEqual(
            proposals,
            [
                "add a retry to the flaky sync step so one transient network "
                "error does not fail the whole checkpoint"
            ],
        )

    def test_long_item_is_truncated(self) -> None:
        long_item = "x" * 400
        text = f"## Proposed workflow changes\n\n- {long_item}\n"
        proposals = _extract_proposed_changes(text)
        self.assertEqual(len(proposals), 1)
        self.assertLessEqual(len(proposals[0]), 241)
        self.assertTrue(proposals[0].endswith("…"))

    def test_missing_section_returns_empty(self) -> None:
        self.assertEqual(_extract_proposed_changes("# Postmortem\n\nNo section.\n"), [])

    def test_section_ends_at_next_heading(self) -> None:
        proposals = _extract_proposed_changes(NUMBERED_POSTMORTEM)
        self.assertNotIn("Not proposals.", " ".join(proposals))


class SyncProposedFixesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp = Path(self._tmpdir.name)
        self.postmortem = self.tmp / "postmortem.md"
        self.tracker = self.tmp / "proposed-fixes.md"

    def test_appends_new_proposals(self) -> None:
        self.postmortem.write_text(DASHED_POSTMORTEM, encoding="utf-8")
        self.tracker.write_text("# Tracker\n\n## Proposals\n", encoding="utf-8")

        appended = _sync_proposed_fixes(
            "user-roles", self.postmortem, self.tracker, "2026-07-02"
        )

        self.assertEqual(appended, 2)
        text = self.tracker.read_text(encoding="utf-8")
        self.assertIn(
            "- [user-roles, 2026-07-02] [engine] Fix the diff-stage expansion-hash mismatch",
            text,
        )

    def test_exact_duplicate_lines_are_skipped(self) -> None:
        self.postmortem.write_text(DASHED_POSTMORTEM, encoding="utf-8")
        self.tracker.write_text("# Tracker\n\n## Proposals\n", encoding="utf-8")

        first = _sync_proposed_fixes("user-roles", self.postmortem, self.tracker, "2026-07-02")
        second = _sync_proposed_fixes("user-roles", self.postmortem, self.tracker, "2026-07-02")

        self.assertEqual(first, 2)
        self.assertEqual(second, 0)
        text = self.tracker.read_text(encoding="utf-8")
        self.assertEqual(text.count("expansion-hash mismatch"), 1)

    def test_same_proposal_from_second_run_is_appended(self) -> None:
        """A recurring proposal from a different slug lands as its own line."""
        self.postmortem.write_text(DASHED_POSTMORTEM, encoding="utf-8")
        self.tracker.write_text("# Tracker\n\n## Proposals\n", encoding="utf-8")

        _sync_proposed_fixes("user-roles", self.postmortem, self.tracker, "2026-07-02")
        appended = _sync_proposed_fixes("dedup-engine-cseries", self.postmortem, self.tracker, "2026-07-03")

        self.assertEqual(appended, 2)
        text = self.tracker.read_text(encoding="utf-8")
        self.assertEqual(text.count("expansion-hash mismatch"), 2)

    def test_missing_postmortem_appends_nothing(self) -> None:
        appended = _sync_proposed_fixes("s", self.tmp / "absent.md", self.tracker, "2026-07-02")
        self.assertEqual(appended, 0)
        self.assertFalse(self.tracker.exists())

    def test_missing_tracker_is_created_with_header(self) -> None:
        self.postmortem.write_text(NO_BOLD_POSTMORTEM, encoding="utf-8")

        appended = _sync_proposed_fixes("s", self.postmortem, self.tracker, "2026-07-02")

        self.assertEqual(appended, 1)
        text = self.tracker.read_text(encoding="utf-8")
        self.assertIn("# Feature Factory — Proposed Fixes Tracker", text)
        self.assertIn("- [s, 2026-07-02] add a retry to the flaky sync step", text)


class CloseoutSyncWiringTests(unittest.TestCase):
    """command_standalone_closeout writes the tracker under the live repo root."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_root = Path(self._tmpdir.name)
        self.runs_root = self.repo_root / "docs" / "workflow" / "feature-runs"
        self.slug = "tracker-slug"
        self.workflow_dir = self.runs_root / self.slug
        self.workflow_dir.mkdir(parents=True, exist_ok=True)

        self._patches: list = []
        for mod in list(gc.get_objects()):
            if not isinstance(mod, types.ModuleType):
                continue
            if getattr(mod, "__name__", "") != "factory_state":
                continue
            if hasattr(mod, "FACTORY_RUNS_ROOT"):
                p = mock.patch.object(mod, "FACTORY_RUNS_ROOT", self.runs_root)
                p.start()
                self._patches.append(p)
            if hasattr(mod, "REPO_ROOT"):
                p = mock.patch.object(mod, "REPO_ROOT", self.repo_root)
                p.start()
                self._patches.append(p)
        self.addCleanup(lambda: [p.stop() for p in self._patches])

        gh_patch = mock.patch.object(
            FACTORY_CMD_CLOSEOUT, "_detect_pr_from_gh", return_value={}
        )
        gh_patch.start()
        self.addCleanup(gh_patch.stop)

    def test_closeout_appends_postmortem_proposals_to_tracker(self) -> None:
        state = FACTORY_STATE._default_workflow_state()
        state["stages"] = {
            "diff": {
                "adversarial_rounds": 1,
                "annotations": [],
                "adversarial_sha_history": [],
                "initial_sha": "",
            }
        }
        FACTORY_STATE.save_workflow_state(self.slug, state)
        (self.workflow_dir / "postmortem.md").write_text(
            NUMBERED_POSTMORTEM, encoding="utf-8"
        )

        args = argparse.Namespace(
            slug=self.slug,
            pr_url="https://example.test/pr/3",
            pr_number=3,
            merge_sha=None,
            note=None,
            out=None,
            skip_experiment_log=None,
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = FACTORY_CMD_CLOSEOUT.command_standalone_closeout(args)

        self.assertEqual(rc, 0)
        tracker = self.repo_root / FACTORY_CMD_CLOSEOUT._PROPOSED_FIXES_REL_PATH
        self.assertTrue(tracker.exists())
        text = tracker.read_text(encoding="utf-8")
        self.assertIn(f"- [{self.slug}, ", text)
        self.assertIn("Make `deliver --create-pr` push the branch first", text)
        self.assertIn("appended 3 new proposal(s)", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
