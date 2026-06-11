"""Regression: a diff that ADDS a new file must round-trip through review.

PR #832 expanded new-file chunks inline so Gemini sees full file content instead
of bare `+`-prefixed lines. The bug it introduced: the diff path ALSO recomputed
the recorded ``artifact_sha256`` over the EXPANDED text, while the staleness
check (``artifact_hash_matches`` -> ``normalized_artifact_text``) re-hashes the
RAW patch on disk. For any slice whose diff adds a new file the two hashes never
matched, so ``checkpoint --stage diff`` stayed permanently "repairable", the
slice index never advanced, and ``closeout`` was blocked.

Expansion is a prompt-presentation concern only; the recorded hash must track the
canonical on-disk patch. This test runs the real Gemini review (CLI mocked)
against a patch that adds a new file and asserts the recorded hash is the
raw-patch hash that validation accepts on a clean rerun.
"""
from __future__ import annotations

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

import run_gemini_review as GM  # noqa: E402  (also puts feature-factory/scripts on sys.path)
import factory_state as FACTORY_STATE  # noqa: E402
from repair_review_checkpoint import parse_frontmatter  # noqa: E402
from workflow_utils import artifact_hash_matches, normalized_artifact_hash  # noqa: E402

FAKE_GEMINI_STDOUT = json.dumps(
    {
        "response": "## Findings\n\n- No issues found.\n\n## Residual Risks\n\n- None.",
        "stats": {},
    }
)

# A diff that edits one file AND adds a new one (the bug's precondition).
RAW_PATCH = (
    "diff --git a/app/existing.py b/app/existing.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/app/existing.py\n"
    "+++ b/app/existing.py\n"
    "@@ -1,2 +1,3 @@\n"
    " line1\n"
    "+added line\n"
    " line2\n"
    "diff --git a/app/newmod.py b/app/newmod.py\n"
    "new file mode 100644\n"
    "index 0000000..3333333\n"
    "--- /dev/null\n"
    "+++ b/app/newmod.py\n"
    "@@ -0,0 +1,2 @@\n"
    "+def hello():\n"
    "+    return 42\n"
)


def _init_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "app").mkdir(parents=True)
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", str(repo)],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.com"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True, capture_output=True, text=True)
    # Files referenced by the diff must exist on disk so expansion can inline the new one.
    (repo / "app" / "existing.py").write_text("line1\nadded line\nline2\n", encoding="utf-8")
    (repo / "app" / "newmod.py").write_text("def hello():\n    return 42\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "--all"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True, text=True)
    return repo


class DiffNewFileHashRoundTrip(unittest.TestCase):
    def test_new_file_diff_records_hash_validation_accepts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = _init_repo(Path(td))
            patch = repo / "implementation.diff.patch"
            patch.write_text(RAW_PATCH, encoding="utf-8")
            review = repo / "diff.gemini.regression-adversarial.review.md"

            real_run = subprocess.run

            def fake_run(cmd, *a, **k):
                # Intercept only the Gemini CLI; let real git calls through.
                if cmd and cmd[0] == "gemini":
                    return subprocess.CompletedProcess(cmd, 0, stdout=FAKE_GEMINI_STDOUT, stderr="")
                return real_run(cmd, *a, **k)

            argv = [
                "run_gemini_review.py",
                "--artifact", str(patch),
                "--lens", "regression-adversarial",
                "--stage", "diff",
                "--output", str(review),
                "--workspace-dir", str(repo),
                "--no-gemini-lock",
            ]
            # Redirect Feature Factory telemetry into the temp tree so
            # record_ai_call doesn't write a state.json into the real repo.
            runs_root = repo / "_runs"
            runs_root.mkdir()
            with mock.patch.object(GM.subprocess, "run", side_effect=fake_run), \
                    mock.patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", runs_root), \
                    mock.patch.object(sys, "argv", argv):
                rc = GM.main()
            self.assertEqual(rc, 0)

            # Precondition sanity: the patch really adds a new file.
            self.assertIn("new file mode", patch.read_text(encoding="utf-8"))

            data, _ = parse_frontmatter(review)
            # The recorded hash must equal the raw-patch hash the validator recomputes,
            # NOT the hash of the expanded prompt text.
            self.assertEqual(data["artifact_sha256"], normalized_artifact_hash("diff", patch))
            # ...so the staleness check accepts the review on a clean rerun.
            self.assertTrue(artifact_hash_matches("diff", patch, data))


if __name__ == "__main__":
    unittest.main()
