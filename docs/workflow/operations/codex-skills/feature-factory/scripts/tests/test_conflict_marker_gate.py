"""Pre-deliver hygiene: find_conflict_markers flags unresolved git conflicts.

Builds a throwaway repo with a feature branch that adds a file containing real
conflict markers, a clean file, and a reStructuredText file whose ``=======``
underline must NOT be mistaken for a conflict separator.
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "factory_deliver.py"
SPEC = importlib.util.spec_from_file_location("factory_deliver", SCRIPT_PATH)
assert SPEC and SPEC.loader
FD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FD
SPEC.loader.exec_module(FD)

_CONFLICTED = (
    "def f():\n"
    "<<<<<<< HEAD\n"
    "    return 1\n"
    "=======\n"
    "    return 2\n"
    ">>>>>>> branch\n"
)
_RST_DOC = "Title\n=======\n\nbody text\n"  # RST underline, not a conflict


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@contextlib.contextmanager
def _chdir(path: Path):
    prior = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prior)


class FindConflictMarkersTests(unittest.TestCase):
    def _build_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "-c", "init.defaultBranch=main", "init", str(repo)],
            check=True, capture_output=True, text=True,
        )
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "T")
        (repo / "clean.txt").write_text("ok\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "base")
        _git(repo, "checkout", "-b", "feature")
        (repo / "conflicted.py").write_text(_CONFLICTED, encoding="utf-8")
        (repo / "clean_new.py").write_text("x = 1\n", encoding="utf-8")
        (repo / "doc.rst").write_text(_RST_DOC, encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feature")
        return repo

    def test_flags_only_the_conflicted_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._build_repo(Path(tmp))
            with patch.object(FD, "REPO_ROOT", repo), _chdir(repo):
                flagged = FD.find_conflict_markers()
            self.assertEqual(flagged, ["conflicted.py"])

    def test_clean_branch_flags_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._build_repo(Path(tmp))
            # Remove the conflict and recommit; the branch is now clean.
            (repo / "conflicted.py").write_text("def f():\n    return 1\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "resolve")
            with patch.object(FD, "REPO_ROOT", repo), _chdir(repo):
                self.assertEqual(FD.find_conflict_markers(), [])


if __name__ == "__main__":
    unittest.main()
