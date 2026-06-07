"""Regression tests for write_canonical_diff's base-ref stability.

A review's artifact_sha256 is the hash of the canonical diff. If that diff were
computed against HEAD, any later commit — including housekeeping commits to fix
scope.json or .gitignore — would change the hash and stale every review, an
unbreakable loop. These tests pin the actual behavior: the diff is computed
against the merge-base, so a commit that advances HEAD but does not touch the
scoped paths leaves the diff (and therefore the review hash) byte-identical.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
WRITE_DIFF = SCRIPT_DIR / "write_canonical_diff.py"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _commit(repo: Path, rel: str, body: str, message: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", message)


def _run_write_diff(repo: Path, output: Path, scope: str, base_ref: str) -> str:
    result = subprocess.run(
        [
            sys.executable,
            str(WRITE_DIFF),
            "--repo",
            str(repo),
            "--output",
            str(output),
            "--path",
            scope,
            "--base-ref",
            base_ref,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"write_canonical_diff failed: {result.stderr or result.stdout}")
    return output.read_text(encoding="utf-8")


class BaseRefStabilityTest(unittest.TestCase):
    def _init_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "-c", "init.defaultBranch=main", "init", str(repo)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test")
        _commit(repo, "app/feature.py", "base\n", "base")
        _git(repo, "checkout", "-b", "feature")
        _commit(repo, "app/feature.py", "changed\n", "feature change")
        return repo

    def test_housekeeping_commit_does_not_change_diff_or_hash(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._init_repo(root)

            diff_before = _run_write_diff(repo, root / "before.diff", "app", "main")
            hash_before = hashlib.sha256(diff_before.encode("utf-8")).hexdigest()
            self.assertIn("changed", diff_before)

            # Housekeeping commit OUTSIDE the scoped paths advances HEAD but must
            # not move the merge-base with main, so the scoped diff is unchanged.
            _commit(repo, "docs/notes.md", "housekeeping\n", "housekeeping")

            diff_after = _run_write_diff(repo, root / "after.diff", "app", "main")
            hash_after = hashlib.sha256(diff_after.encode("utf-8")).hexdigest()

            self.assertEqual(diff_before, diff_after)
            self.assertEqual(hash_before, hash_after)

    def test_meta_records_base_ref_not_head(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._init_repo(root)
            output = root / "scoped.diff"
            _run_write_diff(repo, output, "app", "main")
            meta = json.loads((output.with_suffix(output.suffix + ".json")).read_text(encoding="utf-8"))
            base_sha = _git(repo, "merge-base", "main", "HEAD").strip()
            head_sha = _git(repo, "rev-parse", "HEAD").strip()
            self.assertEqual(meta["git_base_sha"], base_sha)
            self.assertEqual(meta["git_head_sha"], head_sha)
            self.assertNotEqual(meta["git_base_sha"], meta["git_head_sha"])


if __name__ == "__main__":
    unittest.main()
