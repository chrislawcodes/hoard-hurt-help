"""Slice 2 tests — discover CLI append semantics + clear flags.

All tests are integration-level via argparse CLI invocation per Gemini MEDIUM F-04.
"""
import importlib.util
import json
import sys
import tempfile
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
RUN_FACTORY = _load("run_factory")


class DiscoverAppendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.tmp_root = Path(self.tmpdir)
        self.slug = "discover-test"
        self._factory_runs_patch = patch.object(
            FACTORY_STATE, "FACTORY_RUNS_ROOT", self.tmp_root
        )
        self._factory_runs_patch.start()
        FACTORY_STATE.workflow_dir(self.slug).mkdir(parents=True, exist_ok=True)
        state = FACTORY_STATE._default_workflow_state()
        FACTORY_STATE.atomic_json_write(FACTORY_STATE.factory_state_path(self.slug), state)
        # Ensure ensure_sync is bypassed for the CLI invocation.
        self._sync_patch = patch.object(RUN_FACTORY, "ensure_sync", lambda: None)
        self._sync_patch.start()

    def tearDown(self) -> None:
        self._factory_runs_patch.stop()
        self._sync_patch.stop()

    def _run(self, argv: list[str]) -> int:
        parser = RUN_FACTORY.build_parser()
        args = parser.parse_args(argv)
        try:
            return args.func(args) or 0
        except SystemExit as exc:
            return int(exc.code) if isinstance(exc.code, int) else 1

    def _load_discovery(self) -> dict:
        state = json.loads(
            FACTORY_STATE.factory_state_path(self.slug).read_text(encoding="utf-8")
        )
        return state.get("discovery", {})

    # US4.1 — append multiple in single invocation
    def test_non_goal_appends_all_values_in_single_call(self) -> None:
        rc = self._run([
            "discover", "--slug", self.slug,
            "--non-goal", "A", "--non-goal", "B",
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(self._load_discovery()["non_goals"], ["A", "B"])

    # US4.2 — append preserves existing
    def test_non_goal_appends_to_existing_list(self) -> None:
        self._run(["discover", "--slug", self.slug, "--non-goal", "A"])
        rc = self._run([
            "discover", "--slug", self.slug,
            "--non-goal", "B", "--non-goal", "C",
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(self._load_discovery()["non_goals"], ["A", "B", "C"])

    # US4.3 — clear empties the list
    def test_clear_non_goals_empties_list(self) -> None:
        self._run([
            "discover", "--slug", self.slug,
            "--non-goal", "A", "--non-goal", "B",
        ])
        rc = self._run(["discover", "--slug", self.slug, "--clear-non-goals"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._load_discovery()["non_goals"], [])

    # US4.4 — clear-then-append in single invocation
    def test_clear_then_append_same_invocation(self) -> None:
        self._run(["discover", "--slug", self.slug, "--non-goal", "A", "--non-goal", "B"])
        rc = self._run([
            "discover", "--slug", self.slug,
            "--clear-non-goals", "--non-goal", "D",
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(self._load_discovery()["non_goals"], ["D"])

    # US4.5 — same semantics for acceptance_criteria
    def test_acceptance_criteria_parallel_behavior(self) -> None:
        rc = self._run([
            "discover", "--slug", self.slug,
            "--acceptance-criteria", "A", "--acceptance-criteria", "B",
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(self._load_discovery()["acceptance_criteria"], ["A", "B"])

        rc = self._run([
            "discover", "--slug", self.slug,
            "--clear-acceptance-criteria", "--acceptance-criteria", "Z",
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(self._load_discovery()["acceptance_criteria"], ["Z"])

    # Edge: empty-string rejected
    def test_empty_string_non_goal_rejected(self) -> None:
        rc = self._run(["discover", "--slug", self.slug, "--non-goal", ""])
        self.assertEqual(rc, 1)
        self.assertEqual(self._load_discovery().get("non_goals", []), [])

    # Edge: whitespace-only rejected
    def test_whitespace_only_non_goal_rejected(self) -> None:
        rc = self._run(["discover", "--slug", self.slug, "--non-goal", "   "])
        self.assertEqual(rc, 1)

    # Edge: dedup keeps single occurrence
    def test_dedup_exact_match(self) -> None:
        rc = self._run([
            "discover", "--slug", self.slug,
            "--non-goal", "A", "--non-goal", "A",
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(self._load_discovery()["non_goals"], ["A"])

    # Edge: interleaved flags — clear still applies before any append
    def test_interleaved_clear_and_appends(self) -> None:
        """Per argparse, the action=append collects all values regardless of flag
        order; clear flag also ends up True. Per FR-014 docs, clear applies first."""
        self._run([
            "discover", "--slug", self.slug,
            "--non-goal", "A", "--non-goal", "B",
        ])
        rc = self._run([
            "discover", "--slug", self.slug,
            "--non-goal", "X", "--clear-non-goals", "--non-goal", "Y",
        ])
        self.assertEqual(rc, 0)
        # Clear runs first, then appends — documented behavior per FR-014.
        self.assertEqual(self._load_discovery()["non_goals"], ["X", "Y"])


if __name__ == "__main__":
    unittest.main()
