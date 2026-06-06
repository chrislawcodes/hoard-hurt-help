"""Tests for factory_pollution_check.auto_revert_polluted_state."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1]

POLLUTION_SPEC = importlib.util.spec_from_file_location(
    "factory_pollution_check", SCRIPT_DIR / "factory_pollution_check.py"
)
assert POLLUTION_SPEC and POLLUTION_SPEC.loader
POLLUTION_CHECK = importlib.util.module_from_spec(POLLUTION_SPEC)
sys.modules[POLLUTION_SPEC.name] = POLLUTION_CHECK
POLLUTION_SPEC.loader.exec_module(POLLUTION_CHECK)


def _make_porcelain_line(rel_path: str, status: str = " M") -> str:
    """Return a git --porcelain status line."""
    return f"{status} {rel_path}"


class PollutionCheckTests(unittest.TestCase):
    """factory_pollution_check.auto_revert_polluted_state behaviour."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._fake_root = Path(self._tmpdir.name)

    def _run(self, porcelain_output: str, checkout_returncode: int = 0) -> list[str]:
        """Run auto_revert_polluted_state with mocked git calls."""
        fake_status = subprocess.CompletedProcess(
            ["git", "status"], 0, stdout=porcelain_output, stderr=""
        )
        fake_checkout = subprocess.CompletedProcess(
            ["git", "checkout"], checkout_returncode, stdout="", stderr=""
        )
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if "status" in cmd:
                return fake_status
            if "checkout" in cmd:
                return fake_checkout
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(POLLUTION_CHECK.subprocess, "run", side_effect=fake_run):
            result = POLLUTION_CHECK.auto_revert_polluted_state(repo_root=self._fake_root)

        self._calls = calls
        return result

    def test_reverts_dirty_state_json_for_known_slug(self) -> None:
        """Known-slug dirty state.json triggers a git checkout."""
        # ff-gc-checkpoint is in KNOWN_TEST_ONLY_SLUGS
        slug = next(iter(POLLUTION_CHECK.KNOWN_TEST_ONLY_SLUGS))
        porcelain = _make_porcelain_line(
            f"docs/workflow/feature-runs/{slug}/state.json"
        )
        reverted = self._run(porcelain)
        self.assertEqual(reverted, [slug])
        checkout_calls = [c for c in self._calls if "checkout" in c]
        self.assertEqual(len(checkout_calls), 1)
        self.assertIn(f"docs/workflow/feature-runs/{slug}/state.json", checkout_calls[0])

    def test_does_not_touch_unknown_slug(self) -> None:
        """A dirty state.json for an unknown slug is left alone."""
        porcelain = _make_porcelain_line(
            "docs/workflow/feature-runs/real-production-slug/state.json"
        )
        reverted = self._run(porcelain)
        self.assertEqual(reverted, [])
        checkout_calls = [c for c in self._calls if "checkout" in c]
        self.assertEqual(len(checkout_calls), 0)

    def test_skips_when_env_var_set(self) -> None:
        """FF_NO_POLLUTION_AUTO_CLEAN=1 disables all revert logic."""
        slug = next(iter(POLLUTION_CHECK.KNOWN_TEST_ONLY_SLUGS))
        porcelain = _make_porcelain_line(
            f"docs/workflow/feature-runs/{slug}/state.json"
        )
        with patch.dict("os.environ", {"FF_NO_POLLUTION_AUTO_CLEAN": "1"}):
            reverted = POLLUTION_CHECK.auto_revert_polluted_state(repo_root=self._fake_root)
        self.assertEqual(reverted, [])

    def test_ignores_staged_only_changes(self) -> None:
        """Files with only staged changes (M in index column) are not reverted."""
        slug = next(iter(POLLUTION_CHECK.KNOWN_TEST_ONLY_SLUGS))
        # "M " means staged (index), not unstaged (worktree)
        porcelain = _make_porcelain_line(
            f"docs/workflow/feature-runs/{slug}/state.json",
            status="M ",
        )
        reverted = self._run(porcelain)
        self.assertEqual(reverted, [])

    def test_ignores_non_state_json_files(self) -> None:
        """Non-state.json files under a known slug are not reverted."""
        slug = next(iter(POLLUTION_CHECK.KNOWN_TEST_ONLY_SLUGS))
        porcelain = _make_porcelain_line(
            f"docs/workflow/feature-runs/{slug}/spec.md"
        )
        reverted = self._run(porcelain)
        self.assertEqual(reverted, [])


if __name__ == "__main__":
    unittest.main()
