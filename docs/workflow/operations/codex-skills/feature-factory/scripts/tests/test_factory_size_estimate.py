"""Unit tests for factory_size_estimate.estimate_size()."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_state as FACTORY_STATE  # noqa: E402
import factory_size_estimate as SIZE_EST  # noqa: E402


class SizeEstimateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_root = Path(self._tmpdir.name)
        self.runs_root = self.repo_root / "docs" / "workflow" / "feature-runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)

        self._repo_patch = mock.patch.object(FACTORY_STATE, "REPO_ROOT", self.repo_root)
        self._runs_patch = mock.patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", self.runs_root)
        self._size_repo_patch = mock.patch.object(SIZE_EST.factory_state, "REPO_ROOT", self.repo_root)
        self._size_runs_patch = mock.patch.object(SIZE_EST.factory_state, "FACTORY_RUNS_ROOT", self.runs_root)
        self._repo_patch.start()
        self._runs_patch.start()
        self._size_repo_patch.start()
        self._size_runs_patch.start()
        self.addCleanup(self._repo_patch.stop)
        self.addCleanup(self._runs_patch.stop)
        self.addCleanup(self._size_repo_patch.stop)
        self.addCleanup(self._size_runs_patch.stop)

    def _write_scope(self, slug: str, paths: list[str]) -> None:
        slug_dir = self.runs_root / slug
        slug_dir.mkdir(parents=True, exist_ok=True)
        (slug_dir / "scope.json").write_text(
            json.dumps({"paths": paths}), encoding="utf-8"
        )

    def _write_state(self, slug: str, *, summary: str = "") -> None:
        slug_dir = self.runs_root / slug
        slug_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "discovery": {"summary": summary},
        }
        (slug_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def _no_diff(self) -> mock.MagicMock:
        """Patch _git_diff_stats to return (None, None) — no diff yet."""
        return mock.patch.object(SIZE_EST, "_git_diff_stats", return_value=(None, None))

    def _with_diff(self, diff_lines: int, changed_files: int) -> mock.MagicMock:
        return mock.patch.object(SIZE_EST, "_git_diff_stats", return_value=(diff_lines, changed_files))

    def test_small_classification(self) -> None:
        # 3 scope paths exceeds the trivial ceiling (≤2) but is within small (≤3).
        # 350-char summary exceeds trivial ceiling (<300) but is within small (<500).
        self._write_scope("alpha", [
            "cloud/apps/api/src/foo.ts",
            "cloud/apps/web/src/bar.tsx",
            "cloud/apps/web/src/baz.tsx",
        ])
        self._write_state("alpha", summary="A" * 350)
        with self._no_diff():
            result = SIZE_EST.estimate_size("alpha")
        self.assertEqual(result["size"], "small")
        self.assertEqual(result["recommended_path"], "quick")
        self.assertEqual(result["signals"]["scope_path_count"], 3)
        self.assertEqual(result["signals"]["summary_chars"], 350)
        self.assertIsNone(result["signals"]["diff_lines"])
        self.assertIn("3 scope paths", result["reasoning"])
        self.assertIn("350-char summary", result["reasoning"])

    def test_medium_classification_many_scope_paths(self) -> None:
        self._write_scope("beta", [f"cloud/path{i}.ts" for i in range(5)])
        self._write_state("beta", summary="B" * 300)
        with self._no_diff():
            result = SIZE_EST.estimate_size("beta")
        self.assertEqual(result["size"], "medium")
        self.assertEqual(result["recommended_path"], "full")
        self.assertIn("medium", result["reasoning"])

    def test_large_classification_many_scope_paths(self) -> None:
        self._write_scope("gamma", [f"cloud/path{i}.ts" for i in range(12)])
        self._write_state("gamma", summary="G" * 100)
        with self._no_diff():
            result = SIZE_EST.estimate_size("gamma")
        self.assertEqual(result["size"], "large")
        self.assertEqual(result["recommended_path"], "full")
        self.assertIn("large", result["reasoning"])

    def test_large_classification_from_diff(self) -> None:
        self._write_scope("delta", ["cloud/one.ts"])
        self._write_state("delta", summary="D" * 100)
        with self._with_diff(diff_lines=900, changed_files=8):
            result = SIZE_EST.estimate_size("delta")
        self.assertEqual(result["size"], "large")
        self.assertEqual(result["signals"]["diff_lines"], 900)
        self.assertEqual(result["signals"]["changed_files"], 8)

    def test_missing_scope_json_returns_medium(self) -> None:
        # Only write state, no scope.json
        self._write_state("epsilon", summary="E" * 100)
        with self._no_diff():
            result = SIZE_EST.estimate_size("epsilon")
        self.assertEqual(result["size"], "medium")
        self.assertEqual(result["recommended_path"], "full")
        self.assertIn("scope.json missing", result["reasoning"])
        self.assertIsNone(result["signals"]["diff_lines"])


    def test_trivial_classification_no_diff(self) -> None:
        """Feature with 1 scope path and short summary is classified trivial."""
        self._write_scope("trivial-a", ["cloud/apps/web/src/components/Foo.tsx"])
        self._write_state("trivial-a", summary="A" * 100)
        with self._no_diff():
            result = SIZE_EST.estimate_size("trivial-a")
        self.assertEqual(result["size"], "trivial")
        self.assertEqual(result["recommended_path"], "none")
        self.assertIn("trivial", result["reasoning"])

    def test_trivial_classification_with_diff(self) -> None:
        """Feature with tiny diff (< 100 lines, ≤ 3 files) is classified trivial."""
        self._write_scope("trivial-b", ["cloud/apps/web/src/components/Bar.tsx"])
        self._write_state("trivial-b", summary="B" * 150)
        with self._with_diff(diff_lines=50, changed_files=2):
            result = SIZE_EST.estimate_size("trivial-b")
        self.assertEqual(result["size"], "trivial")
        self.assertEqual(result["recommended_path"], "none")

    def test_trivial_boundary_just_above_into_small(self) -> None:
        """A feature at the exact trivial ceiling tips into small, not trivial.

        3 scope paths exceeds the trivial max (2) but is within the small band (≤3).
        summary_chars=400 exceeds trivial max (300) but is within small (<500).
        Result should be 'small' with recommended_path 'quick'.
        """
        self._write_scope("boundary-c", [f"cloud/path{i}.ts" for i in range(3)])
        self._write_state("boundary-c", summary="C" * 400)
        with self._no_diff():
            result = SIZE_EST.estimate_size("boundary-c")
        self.assertEqual(result["size"], "small")
        self.assertEqual(result["recommended_path"], "quick")

    def test_trivial_rejected_by_diff_lines(self) -> None:
        """A feature with ≥100 diff lines is not trivial even if other signals are small."""
        self._write_scope("trivial-d", ["cloud/apps/web/src/Foo.tsx"])
        self._write_state("trivial-d", summary="D" * 100)
        with self._with_diff(diff_lines=100, changed_files=2):
            result = SIZE_EST.estimate_size("trivial-d")
        # 100 diff lines fails the < 100 trivial check, so must be small or medium.
        self.assertNotEqual(result["size"], "trivial")
        self.assertNotEqual(result["recommended_path"], "none")


if __name__ == "__main__":
    unittest.main()
