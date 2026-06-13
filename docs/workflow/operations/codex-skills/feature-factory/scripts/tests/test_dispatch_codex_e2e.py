import argparse
import contextlib
import importlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import factory_cmd_dispatch
import factory_deliver
import factory_state
import run_factory


_REAL_POPEN = subprocess.Popen
_REAL_RUN = subprocess.run

SLUG = "dispatch-e2e"
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_PROMPT = "Dispatch Codex on this prompt."


class DispatchCodexE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        global factory_cmd_dispatch, factory_deliver, factory_state, run_factory
        factory_cmd_dispatch = importlib.import_module("factory_cmd_dispatch")
        factory_deliver = importlib.import_module("factory_deliver")
        factory_state = importlib.import_module("factory_state")
        run_factory = importlib.import_module("run_factory")

        self.repo_dir_obj = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.repo_dir_obj.name)
        self.runs_root_obj = tempfile.TemporaryDirectory()
        self.runs_root = Path(self.runs_root_obj.name)
        self.addCleanup(self.repo_dir_obj.cleanup)
        self.addCleanup(self.runs_root_obj.cleanup)
        self._old_cwd = Path.cwd()
        os.chdir(self.repo_dir)
        self.addCleanup(lambda: os.chdir(self._old_cwd))

        init_result = subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
        )
        if init_result.returncode != 0:
            subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
                cwd=self.repo_dir,
                check=True,
                capture_output=True,
                text=True,
            )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo_dir, check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        (self.repo_dir / "README.md").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_dir, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "baseline"], cwd=self.repo_dir, check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        self.base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        self.slug = SLUG
        slug_root = self.runs_root / self.slug
        slug_root.mkdir(parents=True, exist_ok=True)
        (slug_root / "reviews").mkdir(exist_ok=True)

        self._runs_root_patch = mock.patch.object(factory_state, "FACTORY_RUNS_ROOT", self.runs_root)
        self._runs_root_patch.start()
        self.addCleanup(self._runs_root_patch.stop)

        for module, attr in (
            (factory_state, "REPO_ROOT"),
            (factory_deliver, "REPO_ROOT"),
        ):
            patcher = mock.patch.object(module, attr, self.repo_dir)
            patcher.start()
            self.addCleanup(patcher.stop)

        factory_state.update_workflow_state(self.slug, lambda state: state.setdefault("codex_dispatches", []))
        state_after_init = factory_state.load_workflow_state(self.slug)
        self.assertIn("stages", state_after_init)
        self.assertIn("codex_dispatches", state_after_init)

        self.codex_path = self.repo_dir / ".bin" / "codex"
        self.codex_path.parent.mkdir(parents=True, exist_ok=True)
        self.codex_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.codex_path.chmod(0o755)
        which_patcher = mock.patch.object(factory_cmd_dispatch.shutil, "which", return_value=str(self.codex_path))
        which_patcher.start()
        self.addCleanup(which_patcher.stop)

    def _dispatch_args(self, prompt_path: Path, extra_args: list[str] | None = None) -> argparse.Namespace:
        return run_factory.build_parser().parse_args(
            [
                "dispatch-codex",
                "--slug",
                self.slug,
                "--prompt-path",
                str(prompt_path),
                "--model",
                DEFAULT_MODEL,
                *(extra_args or []),
            ]
        )

    def _read_state(self) -> dict:
        return json.loads(factory_state.factory_state_path(self.slug).read_text(encoding="utf-8"))

    def _current_head_sha(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _commit_count(self) -> int:
        return int(
            subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=self.repo_dir,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )

    def _latest_commit_message(self) -> str:
        return subprocess.run(
            ["git", "log", "-1", "--pretty=%B"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _simulate_codex_changes(self, changes: dict[str, str | None], *, commit: bool) -> None:
        for rel_path, content in changes.items():
            target = self.repo_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(content, encoding="utf-8")
        if commit:
            subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "commit", "-m", "codex changes"],
                cwd=self.repo_dir,
                check=True,
                capture_output=True,
                text=True,
            )

    def _simulate_codex_writing(self, n_lines: int, *, commit: bool = True) -> None:
        target = self.repo_dir / "feature.py"
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        new_content = existing + "\n".join(f"x{i} = {i}" for i in range(n_lines)) + "\n"
        self._simulate_codex_changes({"feature.py": new_content}, commit=commit)

    def _argv_scoped_popen(self, n_lines_codex_writes: int = 500, returncode: int = 0, *, commit_changes: bool = True, edit_changes: dict[str, str | None] | None = None):
        captured_real = _REAL_POPEN
        test = self

        class FakePopen:
            def __init__(self, *args, **kwargs):
                argv = args[0] if args else kwargs.get("args")
                if isinstance(argv, (list, tuple)):
                    argv_list = list(argv)
                elif argv is None:
                    argv_list = []
                else:
                    argv_list = [argv]
                self.args = argv_list
                if argv_list and argv_list[0] == str(test.codex_path):
                    # Always simulate the Codex write so the post-dispatch
                    # freshness path sees a real code delta, even when we force
                    # a non-zero exit for the record.
                    if edit_changes is not None:
                        test._simulate_codex_changes(edit_changes, commit=commit_changes)
                    else:
                        test._simulate_codex_writing(n_lines_codex_writes, commit=commit_changes)
                    self._is_mock = True
                    self._returncode = returncode
                    self._stdout = ""
                    self._stderr = ""
                    self._real = None
                    # The shared runner streams stdout/stderr line-by-line.
                    self.stdout = io.StringIO(self._stdout)
                    self.stderr = io.StringIO(self._stderr)
                else:
                    self._is_mock = False
                    self._real = captured_real(*args, **kwargs)
                    self.stdout = self._real.stdout
                    self.stderr = self._real.stderr

            def communicate(self, input=None, timeout=None):
                if self._is_mock:
                    return (self._stdout, self._stderr)
                return self._real.communicate(input=input, timeout=timeout)

            def poll(self):
                if self._is_mock:
                    return self._returncode
                return self._real.poll()

            @property
            def returncode(self):
                if self._is_mock:
                    return self._returncode
                return self._real.returncode

            @property
            def pid(self):
                if self._is_mock:
                    return 99999
                return self._real.pid

            def wait(self, timeout=None):
                if self._is_mock:
                    return self._returncode
                return self._real.wait(timeout=timeout)

            def kill(self):
                if not self._is_mock:
                    self._real.kill()

            def terminate(self):
                if not self._is_mock:
                    self._real.terminate()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                if not self._is_mock:
                    return self._real.__exit__(exc_type, exc_val, exc_tb)
                return None

        return mock.patch.object(factory_cmd_dispatch.subprocess, "Popen", FakePopen)

    def _run_dispatch(self, popen_cm, run_cm=None, prompt_text: str = DEFAULT_PROMPT, extra_args: list[str] | None = None):
        prompt_path = self.repo_dir / "prompt.txt"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        args = self._dispatch_args(prompt_path, extra_args=extra_args)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            stack.enter_context(popen_cm)
            if run_cm is not None:
                stack.enter_context(run_cm)
            try:
                rc = args.func(args)
            except SystemExit as exc:
                rc = exc.code
        return rc, stdout.getvalue(), stderr.getvalue(), prompt_path

    def test_e2e_canonical_suppression(self) -> None:
        rc, _, _, _ = self._run_dispatch(self._argv_scoped_popen(500, 0))
        self.assertEqual(rc, 0)

        state = self._read_state()
        record = state["codex_dispatches"][-1]
        post_dispatch_head = self._current_head_sha()
        self.assertEqual(record["lines_added_at_dispatch_time"], 500)
        self.assertEqual(record["head_sha"], post_dispatch_head)
        self.assertEqual(record["branch_base_sha"], self.base_sha)

        with mock.patch.object(factory_deliver, "_resolve_branch_base", return_value=self.base_sha), \
            mock.patch.object(factory_deliver, "_added_code_lines", return_value=500), \
            mock.patch.object(factory_deliver, "is_ancestor_of_head", return_value=True):
            status, note = factory_deliver.check_implementation_rule(self.slug)
        self.assertEqual(status, "suppressed")
        self.assertIn(record["ts"], note)
        self.assertIn(record["head_sha"][:7], note)
        self.assertIn("+500", note)

    def test_e2e_drift_triggers(self) -> None:
        rc, _, _, _ = self._run_dispatch(self._argv_scoped_popen(500, 0))
        self.assertEqual(rc, 0)
        self._simulate_codex_writing(100)

        with mock.patch.object(factory_deliver, "_resolve_branch_base", return_value=self.base_sha), \
            mock.patch.object(factory_deliver, "_added_code_lines", return_value=600), \
            mock.patch.object(factory_deliver, "is_ancestor_of_head", return_value=True):
            status, note = factory_deliver.check_implementation_rule(self.slug)
        self.assertEqual(status, "triggered")
        self.assertIn("600", note)

    def test_e2e_added_code_lines_failure_recompute(self) -> None:
        def side_effect(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd[:2] == ["git", "diff"]:
                raise FileNotFoundError(2, "No such file or directory", "git")
            return _REAL_RUN(cmd, *args, **kwargs)

        rc, _, _, _ = self._run_dispatch(
            self._argv_scoped_popen(500, 0),
            run_cm=mock.patch.object(
                factory_deliver.subprocess,
                "run",
                side_effect=side_effect,
            ),
        )
        self.assertEqual(rc, 0)

        record = self._read_state()["codex_dispatches"][-1]
        self.assertIsNone(record["lines_added_at_dispatch_time"])
        self.assertIsNotNone(record["head_sha"])
        self.assertEqual(record["branch_base_sha"], self.base_sha)

        with mock.patch.object(factory_deliver, "_resolve_branch_base", return_value=self.base_sha), \
            mock.patch.object(factory_deliver, "_added_code_lines", return_value=500), \
            mock.patch.object(factory_deliver, "is_ancestor_of_head", return_value=True), \
            mock.patch.object(factory_deliver, "_recompute_lines_for_dispatch", return_value=500) as recompute:
            status, note = factory_deliver.check_implementation_rule(self.slug)
        self.assertEqual(status, "suppressed")
        self.assertIn(record["ts"], note)
        recompute.assert_called_once()

    def test_e2e_non_zero_codex_records(self) -> None:
        rc, _, _, _ = self._run_dispatch(self._argv_scoped_popen(500, returncode=1))
        self.assertEqual(rc, 1)

        state = self._read_state()
        record = state["codex_dispatches"][-1]
        self.assertEqual(record["exit_code"], 1)
        self.assertEqual(record["lines_added_at_dispatch_time"], 500)

        with mock.patch.object(factory_deliver, "_resolve_branch_base", return_value=self.base_sha), \
            mock.patch.object(factory_deliver, "_added_code_lines", return_value=500), \
            mock.patch.object(factory_deliver, "is_ancestor_of_head", return_value=True):
            status, note = factory_deliver.check_implementation_rule(self.slug)
        self.assertEqual(status, "triggered")
        self.assertIn("500", note)

    def test_e2e_rev_parse_head_failure(self) -> None:
        def side_effect(cmd, *args, **kwargs):
            if (
                isinstance(cmd, list)
                and len(cmd) >= 3
                and cmd[0] == "git"
                and cmd[1] == "rev-parse"
                and cmd[2] == "HEAD"
            ):
                raise FileNotFoundError(2, "No such file or directory", "git")
            return _REAL_RUN(cmd, *args, **kwargs)

        rc, _, _, _ = self._run_dispatch(
            self._argv_scoped_popen(500, 0),
            run_cm=mock.patch.object(factory_cmd_dispatch.subprocess, "run", side_effect=side_effect),
        )
        self.assertEqual(rc, 0)

        record = self._read_state()["codex_dispatches"][-1]
        self.assertIsNone(record["head_sha"])
        self.assertEqual(record["branch_base_sha"], self.base_sha)
        self.assertIsNotNone(record["lines_added_at_dispatch_time"])

        with mock.patch.object(factory_deliver, "_resolve_branch_base", return_value=self.base_sha), \
            mock.patch.object(factory_deliver, "_added_code_lines", return_value=500), \
            mock.patch.object(factory_deliver, "is_ancestor_of_head", return_value=False):
            status, note = factory_deliver.check_implementation_rule(self.slug)
        self.assertEqual(status, "triggered")
        self.assertIn("500", note)

    def test_e2e_auto_commit_creates_commit_and_updates_state(self) -> None:
        rc, _, _, prompt_path = self._run_dispatch(
            self._argv_scoped_popen(
                returncode=0,
                commit_changes=False,
                edit_changes={
                    "feature_a.py": "alpha = 1\n",
                    "feature_b.py": "beta = 2\n",
                },
            )
        )
        self.assertEqual(rc, 0)
        self.assertEqual(self._commit_count(), 2)
        state = self._read_state()
        record = state["codex_dispatches"][-1]
        self.assertEqual(record["head_sha"], self._current_head_sha())
        self.assertEqual(record["auto_commit"]["commit_sha"], self._current_head_sha())
        self.assertEqual(record["auto_commit"]["introduced_paths"], ["feature_a.py", "feature_b.py"])
        expected_prompt_sha = hashlib.sha256(DEFAULT_PROMPT.encode("utf-8")).hexdigest()
        expected_message = (
            "dispatch-codex auto-commit: prompt.txt\n\n"
            f"Prompt sha256: {expected_prompt_sha}\n"
            f"Dispatch ID: {record['ts']}\n"
            f"Model: {DEFAULT_MODEL}\n\n"
            f"Co-Authored-By: Codex ({DEFAULT_MODEL}) <noreply@openai.com>"
        )
        self.assertEqual(self._latest_commit_message(), expected_message)
        self.assertEqual(record["prompt_path"], str(prompt_path))

    def test_e2e_auto_commit_preserves_operator_dirty_file(self) -> None:
        operator_file = self.repo_dir / "operator.py"
        operator_file.write_text("operator = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "operator.py"], cwd=self.repo_dir, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "add operator file"], cwd=self.repo_dir, check=True, capture_output=True, text=True)
        operator_file.write_text("operator = 2\n", encoding="utf-8")

        rc, _, _, _ = self._run_dispatch(
            self._argv_scoped_popen(
                returncode=0,
                commit_changes=False,
                edit_changes={"feature.py": "feature = 1\n"},
            )
        )
        self.assertEqual(rc, 0)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn(" M operator.py", status)
        self.assertNotIn("feature.py", status)
        record = self._read_state()["codex_dispatches"][-1]
        self.assertEqual(record["auto_commit"]["introduced_paths"], ["feature.py"])

    def test_e2e_auto_commit_skips_on_operator_overlap(self) -> None:
        foo_file = self.repo_dir / "foo.py"
        foo_file.write_text("foo = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "foo.py"], cwd=self.repo_dir, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "add foo file"], cwd=self.repo_dir, check=True, capture_output=True, text=True)
        foo_file.write_text("foo = 2\n", encoding="utf-8")

        rc, _, stderr, _ = self._run_dispatch(
            self._argv_scoped_popen(
                returncode=0,
                commit_changes=False,
                edit_changes={"foo.py": "foo = 3\n"},
            )
        )
        self.assertEqual(rc, 0)
        self.assertIn("overlap with operator dirty", stderr)
        self.assertEqual(self._commit_count(), 2)
        record = self._read_state()["codex_dispatches"][-1]
        self.assertTrue(record["auto_commit"]["skipped"])
        self.assertEqual(record["auto_commit"]["reason"], "overlap with operator dirty")
        self.assertEqual(record["auto_commit"]["overlap_paths"], ["foo.py"])

    def test_e2e_no_auto_commit_flag_leaves_dirty(self) -> None:
        rc, _, _, _ = self._run_dispatch(
            self._argv_scoped_popen(
                returncode=0,
                commit_changes=False,
                edit_changes={
                    "feature_a.py": "alpha = 1\n",
                    "feature_b.py": "beta = 2\n",
                },
            ),
            extra_args=["--no-auto-commit"],
        )
        self.assertEqual(rc, 0)
        self.assertEqual(self._commit_count(), 1)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("?? feature_a.py", status)
        self.assertIn("?? feature_b.py", status)
        record = self._read_state()["codex_dispatches"][-1]
        self.assertTrue(record["auto_commit"]["skipped"])
        self.assertEqual(record["auto_commit"]["reason"], "--no-auto-commit flag")

    def test_e2e_commit_failure_appends_record_and_returns_one(self) -> None:
        def side_effect(cmd, *args, **kwargs):
            if isinstance(cmd, list) and len(cmd) >= 2 and cmd[:2] == ["git", "commit"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="pre-commit hook rejected")
            return _REAL_RUN(cmd, *args, **kwargs)

        rc, _, stderr, _ = self._run_dispatch(
            self._argv_scoped_popen(
                returncode=0,
                commit_changes=False,
                edit_changes={"feature.py": "feature = 1\n"},
            ),
            run_cm=mock.patch.object(factory_cmd_dispatch.subprocess, "run", side_effect=side_effect),
        )
        self.assertEqual(rc, 1)
        self.assertIn("pre-commit hook rejected", stderr)
        self.assertEqual(self._commit_count(), 1)
        record = self._read_state()["codex_dispatches"][-1]
        self.assertTrue(record["auto_commit"]["skipped"])
        self.assertEqual(record["auto_commit"]["reason"], "git commit failed")

    def test_e2e_deleted_clean_tracked_file_is_auto_committed(self) -> None:
        bar_file = self.repo_dir / "bar.py"
        bar_file.write_text("bar = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "bar.py"], cwd=self.repo_dir, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "add bar file"], cwd=self.repo_dir, check=True, capture_output=True, text=True)

        rc, _, _, _ = self._run_dispatch(
            self._argv_scoped_popen(
                returncode=0,
                commit_changes=False,
                edit_changes={"bar.py": None},
            )
        )
        self.assertEqual(rc, 0)
        self.assertEqual(self._commit_count(), 3)
        record = self._read_state()["codex_dispatches"][-1]
        self.assertIn("bar.py", record["auto_commit"]["introduced_paths"])
        self.assertIn("D\tbar.py", subprocess.run(
            ["git", "log", "-1", "--name-status", "--pretty=format:"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout)


if __name__ == "__main__":
    unittest.main()
