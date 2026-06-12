"""Regression tests for diff base-ref selection (drift after a mid-run rebase).

A mid-run `git rebase` can orphan the last-reviewed head or leave the recorded
base ref pointing at a stale remote *feature* branch. diff_review_budget_state
must never hand a drifted ref/SHA to the next diff (it would sweep unrelated
commits into the reviewed diff); it must validate ancestry and otherwise anchor
to the merge-base with the integration branch.

The selection tests drive the real diff_review_budget_state with the ancestry
checks (is_ancestor_of_head / merge_base_with_default_branch / _git_head_sha)
mocked, so the branching logic is exercised deterministically. A separate test
covers merge_base_with_default_branch against a real git repo.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_state as FS  # noqa: E402
import factory_git as GIT  # noqa: E402
import factory_stages as STAGES  # noqa: E402


def _write_diff_meta(slug: str, *, base_ref: str, base_sha: str, head_sha: str) -> Path:
    artifact = FS.default_artifact_path(slug, "diff")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\n", encoding="utf-8")
    meta = artifact.with_suffix(artifact.suffix + ".json")
    meta.write_text(
        json.dumps({"git_base_ref": base_ref, "git_base_sha": base_sha, "git_head_sha": head_sha}),
        encoding="utf-8",
    )
    return artifact


class DiffBaseRefSelection(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        p = mock.patch.object(FS, "FACTORY_RUNS_ROOT", Path(self._tmp.name))
        p.start()
        self.addCleanup(p.stop)
        FS.workflow_dir("ff-base").mkdir(parents=True, exist_ok=True)

    def _budget(self, *, current_head: str, ancestors: set[str], fork_point: str | None = "FORKPOINT"):
        """Run diff_review_budget_state with ancestry checks mocked."""
        ctx = [
            mock.patch.object(STAGES, "_git_head_sha", return_value=current_head),
            mock.patch.object(STAGES, "is_ancestor_of_head", side_effect=lambda sha: sha in ancestors),
            mock.patch.object(STAGES, "merge_base_with_default_branch", return_value=fork_point),
        ]
        for c in ctx:
            c.start()
            self.addCleanup(c.stop)
        return STAGES.diff_review_budget_state("ff-base")

    def test_stale_recorded_base_ref_falls_back_to_merge_base(self) -> None:
        # The incident: recorded base is a remote feature ref whose SHA is now
        # orphaned (not an ancestor). Must NOT reuse it; anchor to the fork point.
        _write_diff_meta("ff-base", base_ref="origin/feature-x", base_sha="STALEBASE", head_sha="HEAD0")
        state = self._budget(current_head="HEAD0", ancestors=set())  # nothing is an ancestor
        self.assertEqual(state["suggested_base_ref"], "FORKPOINT")
        self.assertEqual(state["scope_basis"], "branch-merge-base")
        self.assertNotEqual(state["suggested_base_ref"], "origin/feature-x")

    def test_valid_recorded_base_sha_is_reused(self) -> None:
        _write_diff_meta("ff-base", base_ref="origin/main", base_sha="GOODBASE", head_sha="HEAD0")
        state = self._budget(current_head="HEAD0", ancestors={"GOODBASE"})
        self.assertEqual(state["suggested_base_ref"], "GOODBASE")
        self.assertEqual(state["scope_basis"], "recorded-base")

    def test_valid_last_reviewed_head_used_for_incremental(self) -> None:
        # HEAD moved since last review; last-reviewed head is still an ancestor.
        _write_diff_meta("ff-base", base_ref="origin/main", base_sha="GOODBASE", head_sha="HEADOLD")
        state = self._budget(current_head="HEADNEW", ancestors={"HEADOLD", "GOODBASE"})
        self.assertEqual(state["suggested_base_ref"], "HEADOLD")
        self.assertEqual(state["scope_basis"], "last-reviewed-head")

    def test_orphaned_last_reviewed_head_falls_back_not_to_orphan(self) -> None:
        # Rebase orphaned the last-reviewed head; recorded base also orphaned.
        _write_diff_meta("ff-base", base_ref="origin/main", base_sha="STALEBASE", head_sha="ORPHAN")
        state = self._budget(current_head="HEADNEW", ancestors=set())
        self.assertEqual(state["suggested_base_ref"], "FORKPOINT")
        self.assertNotEqual(state["suggested_base_ref"], "ORPHAN")

    def test_fresh_run_with_no_meta_leaves_base_empty_without_extra_git(self) -> None:
        # No prior diff recorded -> leave the base empty (write_canonical_diff
        # resolves the first diff itself) and do NOT spend a merge-base git call.
        with mock.patch.object(STAGES, "_git_head_sha", return_value="HEAD0"), \
                mock.patch.object(STAGES, "is_ancestor_of_head", return_value=False), \
                mock.patch.object(STAGES, "merge_base_with_default_branch", return_value="FORKPOINT") as mb:
            state = STAGES.diff_review_budget_state("ff-base")  # no diff meta written
        self.assertEqual(state["suggested_base_ref"], "")
        self.assertEqual(state["scope_basis"], "branch-merge-base")
        mb.assert_not_called()

    def test_stale_base_but_unresolvable_fork_point_leaves_base_empty(self) -> None:
        # Drift case, but merge_base_with_default_branch returns None (e.g. no
        # integration branch fetched) -> fall back to empty rather than crash.
        _write_diff_meta("ff-base", base_ref="origin/feature-x", base_sha="STALE", head_sha="HEAD0")
        state = self._budget(current_head="HEAD0", ancestors=set(), fork_point=None)
        self.assertEqual(state["suggested_base_ref"], "")


class PreferredDiffBaseRefTest(unittest.TestCase):
    def test_explicit_request_wins(self) -> None:
        self.assertEqual(STAGES.preferred_diff_base_ref("s", "origin/main"), "origin/main")

    def test_validated_suggestion_used(self) -> None:
        with mock.patch.object(STAGES, "diff_review_budget_state",
                               return_value={"suggested_base_ref": "VALIDSHA"}):
            self.assertEqual(STAGES.preferred_diff_base_ref("s"), "VALIDSHA")

    def test_fresh_anchors_to_merge_base_not_none(self) -> None:
        # No requested, no suggested -> anchor to merge-base (NOT None, which
        # would defer to write_canonical_diff's @{upstream}-first resolution).
        with mock.patch.object(STAGES, "diff_review_budget_state",
                               return_value={"suggested_base_ref": ""}), \
                mock.patch.object(STAGES, "merge_base_with_default_branch", return_value="FORKPOINT"):
            self.assertEqual(STAGES.preferred_diff_base_ref("s"), "FORKPOINT")

    def test_fresh_falls_back_to_none_when_no_integration_branch(self) -> None:
        with mock.patch.object(STAGES, "diff_review_budget_state",
                               return_value={"suggested_base_ref": ""}), \
                mock.patch.object(STAGES, "merge_base_with_default_branch", return_value=None):
            self.assertIsNone(STAGES.preferred_diff_base_ref("s"))


class MergeBaseWithDefaultBranchTest(unittest.TestCase):
    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    def test_returns_fork_point_with_default_branch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            subprocess.run(["git", "-c", "init.defaultBranch=main", "init", str(repo)],
                           check=True, capture_output=True, text=True)
            self._git(repo, "config", "user.email", "t@e.com")
            self._git(repo, "config", "user.name", "T")
            (repo / "a.txt").write_text("base\n", encoding="utf-8")
            self._git(repo, "add", "--all")
            self._git(repo, "commit", "-m", "base")
            fork = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                  check=True, capture_output=True, text=True).stdout.strip()
            self._git(repo, "checkout", "-b", "feature")
            (repo / "b.txt").write_text("feature\n", encoding="utf-8")
            self._git(repo, "add", "--all")
            self._git(repo, "commit", "-m", "feature work")

            with mock.patch.object(GIT, "REPO_ROOT", repo):
                base = GIT.merge_base_with_default_branch()
            # origin/* candidates don't exist; falls back to local main -> the base commit.
            self.assertEqual(base, fork)


if __name__ == "__main__":
    unittest.main()
