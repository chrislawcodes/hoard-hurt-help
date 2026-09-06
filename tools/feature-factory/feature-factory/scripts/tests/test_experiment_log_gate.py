"""Tests for the experiment auto-log gate (init --experiment → closeout block).

The thin-vs-factory A/B log stayed empty after a real run because nothing
forced the entry. Runs initialized with ``init --experiment <name>`` now
hard-block closeout until the experiment's log file mentions the run's slug,
with an explicit recorded bypass (``--skip-experiment-log "<reason>"``).
Legacy runs (no recorded init SHA) are never gated — the engine's standard
fail-open convention.

All writes land in a per-test temp repo root (never the live repo tree).
"""
import argparse
import gc
import io
import contextlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_state as FACTORY_STATE  # noqa: E402
import factory_cmd_closeout as FACTORY_CMD_CLOSEOUT  # noqa: E402
import run_factory as RUN_FACTORY  # noqa: E402


SLUG = "exp-gate-slug"
EXPERIMENT = "thin-vs-factory"
LOG_REL = FACTORY_STATE.EXPERIMENT_LOG_FILES[EXPERIMENT]


def _patch_factory_state_roots(runs_root: Path, repo_root: Path) -> list:
    """Start patches pointing every live factory_state instance at the tmp roots."""
    patches = []
    for mod in list(gc.get_objects()):
        if not isinstance(mod, types.ModuleType):
            continue
        if getattr(mod, "__name__", "") != "factory_state":
            continue
        if hasattr(mod, "FACTORY_RUNS_ROOT"):
            p = mock.patch.object(mod, "FACTORY_RUNS_ROOT", runs_root)
            p.start()
            patches.append(p)
        if hasattr(mod, "REPO_ROOT"):
            p = mock.patch.object(mod, "REPO_ROOT", repo_root)
            p.start()
            patches.append(p)
    return patches


def _closeout_args(**overrides) -> argparse.Namespace:
    defaults = {
        "slug": SLUG,
        "pr_url": "https://example.test/pr/9",
        "pr_number": 9,
        "merge_sha": None,
        "note": None,
        "out": None,
        "skip_experiment_log": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class ExperimentLogGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_root = Path(self._tmpdir.name)
        self.runs_root = self.repo_root / "docs" / "workflow" / "feature-runs"
        self.workflow_dir = self.runs_root / SLUG
        self.workflow_dir.mkdir(parents=True, exist_ok=True)

        self._patches = _patch_factory_state_roots(self.runs_root, self.repo_root)
        self.addCleanup(lambda: [p.stop() for p in self._patches])

        gh_patch = mock.patch.object(
            FACTORY_CMD_CLOSEOUT, "_detect_pr_from_gh", return_value={}
        )
        gh_patch.start()
        self.addCleanup(gh_patch.stop)

    def _write_state(self, *, experiment: str | None, init_sha: str) -> None:
        state = FACTORY_STATE._default_workflow_state()
        state["stages"] = {
            "diff": {
                "adversarial_rounds": 1,
                "annotations": [],
                "adversarial_sha_history": [],
                "initial_sha": "",
            }
        }
        state[FACTORY_STATE.INIT_HEAD_SHA_KEY] = init_sha
        if experiment:
            state[FACTORY_STATE.EXPERIMENT_KEY] = {"name": experiment}
        FACTORY_STATE.save_workflow_state(SLUG, state)

    def _write_log(self, text: str) -> Path:
        log_path = self.repo_root / LOG_REL
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(text, encoding="utf-8")
        return log_path

    def _run_closeout(self, args: argparse.Namespace) -> int:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = FACTORY_CMD_CLOSEOUT.command_standalone_closeout(args)
        self._stdout = stdout.getvalue()
        self._stderr = stderr.getvalue()
        return rc

    # ------------------------------------------------------------------
    # Gate behavior
    # ------------------------------------------------------------------

    def test_blocks_when_log_has_no_entry_for_slug(self) -> None:
        self._write_state(experiment=EXPERIMENT, init_sha="abc123")
        self._write_log("# Experiments\n\nNo runs logged yet.\n")

        with self.assertRaises(SystemExit) as ctx:
            self._run_closeout(_closeout_args())

        message = str(ctx.exception)
        self.assertIn(EXPERIMENT, message)
        self.assertIn(SLUG, message)
        self.assertIn("--skip-experiment-log", message)
        # Hard block: nothing was written.
        self.assertFalse((self.workflow_dir / "closeout.md").exists())
        reloaded = FACTORY_STATE.load_workflow_state(SLUG)
        self.assertEqual(reloaded.get("delivery"), {})

    def test_blocks_when_log_file_missing(self) -> None:
        self._write_state(experiment=EXPERIMENT, init_sha="abc123")

        with self.assertRaises(SystemExit):
            self._run_closeout(_closeout_args())

    def test_passes_when_log_mentions_slug(self) -> None:
        self._write_state(experiment=EXPERIMENT, init_sha="abc123")
        self._write_log(f"# Experiments\n\n## Run 2 — `{SLUG}` (2026-07-02)\n")

        rc = self._run_closeout(_closeout_args())

        self.assertEqual(rc, 0)
        self.assertTrue((self.workflow_dir / "closeout.md").exists())

    def test_bypass_records_reason_in_state(self) -> None:
        self._write_state(experiment=EXPERIMENT, init_sha="abc123")
        reason = "thin arm abandoned mid-run; nothing to compare"

        rc = self._run_closeout(_closeout_args(skip_experiment_log=reason))

        self.assertEqual(rc, 0)
        reloaded = FACTORY_STATE.load_workflow_state(SLUG)
        self.assertEqual(
            reloaded[FACTORY_STATE.EXPERIMENT_KEY].get("log_skip_reason"), reason
        )
        self.assertIn("experiment-log gate bypassed", self._stderr)

    def test_empty_bypass_reason_rejected(self) -> None:
        self._write_state(experiment=EXPERIMENT, init_sha="abc123")

        with self.assertRaises(SystemExit) as ctx:
            self._run_closeout(_closeout_args(skip_experiment_log="   "))
        self.assertIn("non-empty", str(ctx.exception))

    def test_fail_open_for_legacy_run_without_init_sha(self) -> None:
        """No recorded init SHA → not gated (matches the engine's other gates)."""
        self._write_state(experiment=EXPERIMENT, init_sha="")

        rc = self._run_closeout(_closeout_args())

        self.assertEqual(rc, 0)

    def test_not_gated_without_experiment(self) -> None:
        self._write_state(experiment=None, init_sha="abc123")

        rc = self._run_closeout(_closeout_args())

        self.assertEqual(rc, 0)

    def test_namespace_without_flag_is_tolerated(self) -> None:
        """Older callers build the closeout Namespace without the new attr."""
        self._write_state(experiment=None, init_sha="abc123")
        args = _closeout_args()
        delattr(args, "skip_experiment_log")

        rc = self._run_closeout(args)

        self.assertEqual(rc, 0)

    # ------------------------------------------------------------------
    # init --experiment wiring
    # ------------------------------------------------------------------

    def test_init_parser_accepts_experiment_flag(self) -> None:
        parser = RUN_FACTORY.build_parser()
        args = parser.parse_args(
            ["init", "--slug", SLUG, "--path", "app", "--experiment", EXPERIMENT]
        )
        self.assertEqual(args.experiment, EXPERIMENT)

    def test_closeout_parser_accepts_skip_experiment_log(self) -> None:
        parser = RUN_FACTORY.build_parser()
        args = parser.parse_args(
            ["closeout", "--slug", SLUG, "--skip-experiment-log", "reason text"]
        )
        self.assertEqual(args.skip_experiment_log, "reason text")

    def test_command_init_stores_experiment_name(self) -> None:
        args = argparse.Namespace(slug=SLUG, path=["app"], experiment=EXPERIMENT)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(RUN_FACTORY, "ensure_sync"), \
                mock.patch.object(RUN_FACTORY, "warn_if_primary_checkout"), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = RUN_FACTORY.command_init(args)

        self.assertEqual(rc, 0)
        reloaded = FACTORY_STATE.load_workflow_state(SLUG)
        self.assertEqual(
            reloaded[FACTORY_STATE.EXPERIMENT_KEY].get("name"), EXPERIMENT
        )

    def test_command_init_rejects_unknown_experiment(self) -> None:
        args = argparse.Namespace(slug=SLUG, path=["app"], experiment="not-a-thing")
        with mock.patch.object(RUN_FACTORY, "ensure_sync"), \
                mock.patch.object(RUN_FACTORY, "warn_if_primary_checkout"):
            with self.assertRaises(SystemExit) as ctx:
                RUN_FACTORY.command_init(args)
        message = str(ctx.exception)
        self.assertIn("not-a-thing", message)
        self.assertIn(EXPERIMENT, message)

    def test_command_init_without_experiment_leaves_state_unset(self) -> None:
        args = argparse.Namespace(slug=SLUG, path=["app"], experiment=None)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(RUN_FACTORY, "ensure_sync"), \
                mock.patch.object(RUN_FACTORY, "warn_if_primary_checkout"), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = RUN_FACTORY.command_init(args)

        self.assertEqual(rc, 0)
        reloaded = FACTORY_STATE.load_workflow_state(SLUG)
        self.assertEqual(reloaded[FACTORY_STATE.EXPERIMENT_KEY], {})


if __name__ == "__main__":
    unittest.main()
