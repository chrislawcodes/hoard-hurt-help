"""Tests for factory_cmd_discover — specifically the trivial/no-FF recommendation path."""
from __future__ import annotations

import gc
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


FACTORY_STATE = _load("factory_state")
_load("factory_size_estimate")  # ensure it's in sys.modules before run_factory
RUN_FACTORY = _load("run_factory")


class DiscoverTrivialRecommendationTests(unittest.TestCase):
    """Tests that discover --complete prints the loud skip-FF block for trivial features."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_root = Path(self._tmpdir.name)
        self.runs_root = self.repo_root / "docs" / "workflow" / "feature-runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.slug = "trivial-test-slug"

        # Patch FACTORY_RUNS_ROOT and REPO_ROOT on every loaded factory_state
        # module instance — multiple importlib loads create independent modules,
        # and lazy imports (record_command_telemetry, etc.) resolve via
        # sys.modules at call time. See PR #820 for the full root-cause writeup.
        self._patches: list = []
        for mod in list(gc.get_objects()):
            if not isinstance(mod, types.ModuleType):
                continue
            if getattr(mod, "__name__", "") != "factory_state":
                continue
            if hasattr(mod, "FACTORY_RUNS_ROOT"):
                p = patch.object(mod, "FACTORY_RUNS_ROOT", self.runs_root)
                p.start()
                self._patches.append(p)
            if hasattr(mod, "REPO_ROOT"):
                p = patch.object(mod, "REPO_ROOT", self.repo_root)
                p.start()
                self._patches.append(p)
        self.addCleanup(lambda: [p.stop() for p in self._patches])

        # Bootstrap a minimal workflow state for the slug.
        FACTORY_STATE.workflow_dir(self.slug).mkdir(parents=True, exist_ok=True)
        state = FACTORY_STATE._default_workflow_state()
        FACTORY_STATE.atomic_json_write(FACTORY_STATE.factory_state_path(self.slug), state)

        # Bypass git sync check.
        self._sync_patch = patch.object(RUN_FACTORY, "ensure_sync", lambda: None)
        self._sync_patch.start()
        self.addCleanup(self._sync_patch.stop)

        # Patch factory_cmd_discover.estimate_size directly — this is the
        # reference that command_discover actually calls, regardless of which
        # factory_size_estimate object is in sys.modules at test time.
        # We also need factory_state.FACTORY_RUNS_ROOT patched so that
        # estimate_size (when called in isolation) sees the test root — but
        # since we're replacing estimate_size entirely with a mock, the
        # FACTORY_RUNS_ROOT patch on FACTORY_STATE above is sufficient for
        # writing/reading state.json and scope.json.
        #
        # To get realistic classify behaviour (trivial vs small vs medium)
        # we replace estimate_size with a thin wrapper that returns the real
        # classification but with _git_diff_stats fixed to (None, None).
        import factory_cmd_discover as _fcd
        import factory_size_estimate as _fse

        def _stable_estimate_size(slug: str) -> dict:
            with patch.object(_fse, "_git_diff_stats", return_value=(None, None)):
                # Also ensure _fse.factory_state.FACTORY_RUNS_ROOT is the test root.
                with patch.object(_fse.factory_state, "FACTORY_RUNS_ROOT", self.runs_root):
                    return _fse.estimate_size(slug)

        self._estimate_patch = patch.object(_fcd, "estimate_size", _stable_estimate_size)
        self._estimate_patch.start()
        self.addCleanup(self._estimate_patch.stop)

    def _write_scope(self, paths: list[str]) -> None:
        slug_dir = FACTORY_STATE.workflow_dir(self.slug)
        (slug_dir / "scope.json").write_text(
            json.dumps({"paths": paths}), encoding="utf-8"
        )

    def _write_summary(self, summary: str) -> None:
        state_path = FACTORY_STATE.factory_state_path(self.slug)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.setdefault("discovery", {})["summary"] = summary
        FACTORY_STATE.atomic_json_write(state_path, state)

    def _run_argv(self, argv: list[str]) -> tuple[int, str]:
        """Run the CLI and capture stdout. Returns (exit_code, captured_stdout)."""
        parser = RUN_FACTORY.build_parser()
        args = parser.parse_args(argv)
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            try:
                rc = args.func(args) or 0
            except SystemExit as exc:
                rc = int(exc.code) if isinstance(exc.code, int) else 1
        return rc, buf.getvalue()

    # ------------------------------------------------------------------
    # Test: trivial feature → SKIP FF block is printed
    # ------------------------------------------------------------------
    def test_skip_ff_message_printed_for_trivial_feature(self) -> None:
        """discover --complete prints the loud SKIP FF block when size is trivial."""
        # Trivial: 1 scope path, 150-char summary, no diff.
        self._write_scope(["cloud/apps/web/src/components/Foo.tsx"])
        self._write_summary("A" * 150)

        # Run discover --summary and --complete together.
        rc, output = self._run_argv([
            "discover", "--slug", self.slug,
            "--summary", "A" * 150,
            "--complete", "--force-complete",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("SKIP FF ENTIRELY", output)
        self.assertIn("[ff]", output)
        self.assertIn("trivial", output.lower())

    # ------------------------------------------------------------------
    # Test: --force-path quick overrides the trivial "none" recommendation
    # ------------------------------------------------------------------
    def test_force_path_quick_overrides_trivial(self) -> None:
        """--force-path quick shows the quick recommendation even for a trivial feature."""
        self._write_scope(["cloud/apps/web/src/components/Foo.tsx"])
        self._write_summary("B" * 100)

        rc, output = self._run_argv([
            "discover", "--slug", self.slug,
            "--summary", "B" * 100,
            "--complete", "--force-complete",
            "--force-path", "quick",
        ])
        self.assertEqual(rc, 0)
        # Should NOT show the skip-FF block.
        self.assertNotIn("SKIP FF ENTIRELY", output)
        # Should show quick recommendation.
        self.assertIn("quick", output)

    # ------------------------------------------------------------------
    # Test: non-trivial feature still shows normal recommendation
    # ------------------------------------------------------------------
    def test_no_skip_ff_message_for_small_feature(self) -> None:
        """A small (not trivial) feature does NOT trigger the skip-FF block."""
        # 3 scope paths, 400-char summary → small band.
        self._write_scope([f"cloud/path{i}.ts" for i in range(3)])
        self._write_summary("C" * 400)

        rc, output = self._run_argv([
            "discover", "--slug", self.slug,
            "--summary", "C" * 400,
            "--complete", "--force-complete",
        ])
        self.assertEqual(rc, 0)
        self.assertNotIn("SKIP FF ENTIRELY", output)
        self.assertIn("quick", output)


if __name__ == "__main__":
    unittest.main()
