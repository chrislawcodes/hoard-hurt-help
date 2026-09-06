import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]  # feature-factory/scripts
REVIEW_LENS = SCRIPT_DIR.parents[1] / "review-lens" / "scripts"
for _p in (str(SCRIPT_DIR), str(REVIEW_LENS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import factory_state as FACTORY_STATE  # noqa: E402
import factory_cmd_prepare_claude_reviews as PREPARE  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


class EnsureDiffArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        # Git repo and the factory runs-root live in SEPARATE temp dirs so the
        # runs-root's scope.json/artifacts don't show up as dirty files inside the
        # repo (which would trip write_canonical_diff's dirty-scope guard).
        self._repo_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._repo_tmp.cleanup)
        self._runs_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._runs_tmp.cleanup)
        self.repo = Path(self._repo_tmp.name).resolve()
        self.runs = Path(self._runs_tmp.name).resolve()

        _git(self.repo, "init")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "Test")
        (self.repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "baseline")

        self._repo_patch = patch.object(PREPARE, "REPO_ROOT", self.repo)
        self._repo_patch.start()
        self.addCleanup(self._repo_patch.stop)
        self._runs_patch = patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", self.runs)
        self._runs_patch.start()
        self.addCleanup(self._runs_patch.stop)

        (FACTORY_STATE.reviews_dir("demo")).mkdir(parents=True, exist_ok=True)
        FACTORY_STATE.scope_manifest_path("demo").write_text(
            json.dumps({"paths": ["app.py"], "allowed_dirty_paths": ["app.py"]}),
            encoding="utf-8",
        )
        # An uncommitted in-scope change for the diff to capture.
        (self.repo / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    def test_generates_canonical_diff(self) -> None:
        artifact = FACTORY_STATE.default_artifact_path("demo", "diff")
        PREPARE._ensure_diff_artifact("demo", artifact, [], "HEAD")
        self.assertTrue(artifact.exists())
        text = artifact.read_text(encoding="utf-8")
        self.assertIn("VALUE = 2", text)
        self.assertIn("app.py", text)

    def test_existing_artifact_is_left_untouched(self) -> None:
        artifact = FACTORY_STATE.default_artifact_path("demo", "diff")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("preexisting", encoding="utf-8")
        PREPARE._ensure_diff_artifact("demo", artifact, [], "HEAD")
        self.assertEqual(artifact.read_text(encoding="utf-8"), "preexisting")

    def test_missing_scope_raises(self) -> None:
        FACTORY_STATE.scope_manifest_path("demo").unlink()
        artifact = FACTORY_STATE.default_artifact_path("demo", "diff")
        with self.assertRaises(SystemExit):
            PREPARE._ensure_diff_artifact("demo", artifact, [], "HEAD")


if __name__ == "__main__":
    unittest.main()
