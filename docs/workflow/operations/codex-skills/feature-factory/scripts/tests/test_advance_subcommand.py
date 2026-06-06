import argparse
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SLUG = "advance-test"
FIXED_NOW = datetime(2026, 4, 24, 12, 34, 56, 789012)
FIXED_TS = FIXED_NOW.strftime("%Y%m%dT%H%M%S_%fZ")
FIXED_SHA = "deadbeef"


class AdvanceSubcommandTests(unittest.TestCase):
    def _load_modules(self) -> None:
        if str(SCRIPT_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPT_DIR))
        self.factory_state = _load("factory_state")
        self.advance = _load("factory_cmd_advance")
        self.run_factory = _load("run_factory")

    def setUp(self) -> None:
        self._load_modules()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._root_patch = patch.object(self.factory_state, "FACTORY_RUNS_ROOT", Path(self._tmpdir.name))
        self._root_patch.start()
        self.addCleanup(self._root_patch.stop)
        self._write_state()

    def _write_state(self) -> None:
        root = self.factory_state.workflow_dir(SLUG)
        root.mkdir(parents=True, exist_ok=True)
        state = self.factory_state._default_workflow_state()
        state["schema_version"] = 2
        state["stages"] = {}
        self.factory_state.atomic_json_write(self.factory_state.factory_state_path(SLUG), state)

    def _read_state(self) -> dict:
        return json.loads(self.factory_state.factory_state_path(SLUG).read_text(encoding="utf-8"))

    def _parser(self) -> argparse.ArgumentParser:
        return self.run_factory.build_parser()

    def _run_command(self, reason: str, stage: str = "spec") -> tuple[int, str, str]:
        args = self._parser().parse_args([
            "advance",
            "--slug",
            SLUG,
            "--stage",
            stage,
            "--reason",
            reason,
        ])
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(self.advance.subprocess, "run", return_value=subprocess.CompletedProcess(
            args=["git", "rev-parse", "HEAD"],
            returncode=0,
            stdout=FIXED_SHA + "\n",
            stderr="",
        )), patch.object(self.advance, "datetime") as datetime_mock, \
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            datetime_mock.utcnow.return_value = FIXED_NOW
            rc = args.func(args)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_happy_path_records_stage_and_annotation(self) -> None:
        rc, stdout, stderr = self._run_command("a meaningful reason for advance", stage="diff")

        self.assertEqual(rc, 0)
        self.assertEqual(stderr, "")
        self.assertIn("[workflow] ✓ advance (diff)", stdout)
        state = self._read_state()
        self.assertEqual(state["stages"]["diff"]["judge_next_action"], "advance")
        self.assertEqual(state["annotations"][-1], {
            "stage": "diff",
            "ts": FIXED_TS,
            "reason": "a meaningful reason for advance",
            "head_sha": FIXED_SHA,
        })

    def test_reason_19_chars_rejects_without_mutation(self) -> None:
        before = self._read_state()
        args = self._parser().parse_args([
            "advance",
            "--slug",
            SLUG,
            "--stage",
            "spec",
            "--reason",
            "abcdefghijklmnopqrs",
        ])
        with self.assertRaises(SystemExit) as ctx:
            with patch.object(
                self.advance.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=["git", "rev-parse", "HEAD"],
                    returncode=0,
                    stdout=FIXED_SHA + "\n",
                    stderr="",
                ),
            ), patch.object(self.advance, "datetime") as datetime_mock, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                datetime_mock.utcnow.return_value = FIXED_NOW
                args.func(args)

        self.assertEqual(ctx.exception.code, 2)
        after = self._read_state()
        self.assertEqual(before, after)

    def test_reason_exactly_20_chars_passes(self) -> None:
        rc, _, _ = self._run_command("abcdefghijklmnopqrst")

        self.assertEqual(rc, 0)
        state = self._read_state()
        self.assertEqual(state["stages"]["spec"]["judge_next_action"], "advance")
        self.assertEqual(state["annotations"][-1]["reason"], "abcdefghijklmnopqrst")

    def test_unknown_stage_rejected_by_argparse(self) -> None:
        before = self._read_state()
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                self._parser().parse_args([
                    "advance",
                    "--slug",
                    SLUG,
                    "--stage",
                    "bogus",
                    "--reason",
                    "a meaningful reason for advance",
                ])

        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(before, self._read_state())

    def test_advance_rejects_implementation_stage(self) -> None:
        before = self._read_state()
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                self._parser().parse_args([
                    "advance",
                    "--slug",
                    SLUG,
                    "--stage",
                    "implementation",
                    "--reason",
                    "a meaningful reason for advance",
                ])

        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(before, self._read_state())

    def test_advance_argparse_choices_explicit(self) -> None:
        parser = self._parser()
        subparsers_action = next(
            action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
        )
        advance_parser = subparsers_action.choices["advance"]
        stage_action = next(action for action in advance_parser._actions if getattr(action, "dest", None) == "stage")
        self.assertIn("diff", stage_action.choices)
        self.assertNotIn("implementation", stage_action.choices)

    def test_empty_head_sha_aborts_before_state_mutation(self) -> None:
        """Regression: blank head_sha in annotations[] is forensically useless.

        Pre-fix, _git_head_sha returned "" on any subprocess error and the
        annotation was written with head_sha="" — confusable with "advance
        ran on the empty tree." The fix exits 2 instead.
        """
        before = self._read_state()
        args = self._parser().parse_args([
            "advance",
            "--slug",
            SLUG,
            "--stage",
            "spec",
            "--reason",
            "valid reason longer than minimum",
        ])
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as ctx:
            with patch.object(
                self.advance.subprocess,
                "run",
                side_effect=FileNotFoundError("git not on PATH"),
            ), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(stderr):
                args.func(args)

        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("could not resolve HEAD", stderr.getvalue())
        self.assertEqual(before, self._read_state())

    def test_nonzero_git_returncode_aborts_before_state_mutation(self) -> None:
        """Same as above but covers `git rev-parse HEAD` returning non-zero
        (e.g., REPO_ROOT is not inside a git work tree).
        """
        before = self._read_state()
        args = self._parser().parse_args([
            "advance",
            "--slug",
            SLUG,
            "--stage",
            "spec",
            "--reason",
            "valid reason longer than minimum",
        ])
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as ctx:
            with patch.object(
                self.advance.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=["git", "rev-parse", "HEAD"],
                    returncode=128,
                    stdout="",
                    stderr="fatal: not a git repository\n",
                ),
            ), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(stderr):
                args.func(args)

        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("could not resolve HEAD", stderr.getvalue())
        self.assertEqual(before, self._read_state())

    def test_whitespace_trimmed_reason_below_minimum_rejects(self) -> None:
        before = self._read_state()
        args = self._parser().parse_args([
            "advance",
            "--slug",
            SLUG,
            "--stage",
            "spec",
            "--reason",
            "abcdefghijklmnopqr   ",
        ])
        with self.assertRaises(SystemExit) as ctx:
            with patch.object(
                self.advance.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=["git", "rev-parse", "HEAD"],
                    returncode=0,
                    stdout=FIXED_SHA + "\n",
                    stderr="",
                ),
            ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                args.func(args)

        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(before, self._read_state())


if __name__ == "__main__":
    unittest.main()
