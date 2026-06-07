"""The required discovery checklist: `discover --complete` is gated on a filled
checklist (goal, audience, success criteria, non-goals, constraints/risks) for
real (required) runs; `--force-complete` bypasses it for trivial/skip-FF work.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
RUN = SCRIPTS / "run_factory.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


FCD = _load("factory_cmd_discover")


class MissingChecklistHelperTests(unittest.TestCase):
    def test_empty_discovery_misses_everything(self):
        missing = FCD._missing_checklist_items({})
        self.assertEqual(len(missing), 5)

    def test_full_checklist_misses_nothing(self):
        discovery = {
            "checklist": {"goal": "g", "audience": "a", "constraints": "c"},
            "acceptance_criteria": ["sc"],
            "non_goals": ["ng"],
        }
        self.assertEqual(FCD._missing_checklist_items(discovery), [])

    def test_partial_lists_only_the_gaps(self):
        discovery = {"checklist": {"goal": "g", "audience": "", "constraints": "c"},
                     "acceptance_criteria": ["sc"], "non_goals": []}
        missing = FCD._missing_checklist_items(discovery)
        self.assertTrue(any("audience" in m for m in missing))
        self.assertTrue(any("non-goals" in m for m in missing))
        self.assertFalse(any("goal" == m for m in missing))


class DiscoverCompleteGateTests(unittest.TestCase):
    def setUp(self):
        self.runs = tempfile.mkdtemp()
        self.env = {**os.environ, "FF_FACTORY_RUNS_ROOT": self.runs}
        self.slug = "checklist-test"
        self._run("init", "--slug", self.slug, "--path", "app")

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(RUN), *args],
            env=self.env, capture_output=True, text=True,
        )

    def test_complete_blocked_without_checklist(self):
        r = self._run("discover", "--slug", self.slug, "--summary", "x", "--complete")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checklist is incomplete", r.stderr)

    def test_complete_succeeds_when_checklist_filled(self):
        self._run("discover", "--slug", self.slug,
                  "--goal", "add /healthz", "--audience", "operators",
                  "--acceptance-criteria", "200 in <200ms",
                  "--non-goal", "not a readiness probe",
                  "--constraints", "no DB access")
        r = self._run("discover", "--slug", self.slug, "--complete")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("complete: yes", r.stdout)

    def test_force_complete_bypasses_checklist(self):
        r = self._run("discover", "--slug", self.slug, "--summary", "typo",
                      "--complete", "--force-complete")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
